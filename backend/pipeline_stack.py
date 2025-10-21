from __future__ import annotations

from pathlib import Path
import secrets
import string

from aws_cdk import (
    DockerVolume,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    CfnOutput,
    aws_apigateway as apigateway,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_event_sources,
    aws_lambda_python_alpha as lambda_python,
    aws_ecr_assets as ecr_assets,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_ssm as ssm,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct


class VideoAutomationStack(Stack):
    """Defines the serverless automation workflow for downstream Sora runs."""

    STATUS_SCHEDULE_INDEX = "status-schedule-index"

    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self._stage = stage
        Tags.of(self).add("Stage", stage)

        project_root = Path(__file__).resolve().parents[1]
        lambda_src = Path(__file__).resolve().parent / "lambda_src"

        jobs_table = dynamodb.Table(
            self,
            "VideoJobsTable",
            partition_key=dynamodb.Attribute(
                name="jobId",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        jobs_table.add_global_secondary_index(
            index_name=self.STATUS_SCHEDULE_INDEX,
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="scheduled_datetime",
                type=dynamodb.AttributeType.STRING,
            ),
        )

        output_bucket = s3.Bucket(
            self,
            "VideoArtifactsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        google_credentials_secret = secretsmanager.Secret(
            self,
            "GoogleDriveServiceAccountSecret",
            description="Service account JSON for uploading final videos into Google Drive.",
        )

        shared_layer = lambda_python.PythonLayerVersion(
            self,
            "SharedUtilitiesLayer",
            entry=str(lambda_src / "common_layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_11],
            bundling=lambda_python.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_11.bundling_image,
                command=[
                    "bash",
                    "-c",
                    "mkdir -p /asset-output/python && cp -r /asset-input/python/. /asset-output/python",
                ],
            ),
        )

        media_layer = lambda_python.PythonLayerVersion(
            self,
            "MediaProcessingLayer",
            entry=str(lambda_src / "media_layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_11],
            bundling=lambda_python.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_11.bundling_image,
                command=[
                    "bash",
                    "-c",
                    "mkdir -p /asset-output/python && cp -r /asset-input/python/. /asset-output/python && pip install --no-cache-dir -r requirements.txt --target /asset-output/python --implementation cp --platform manylinux2014_x86_64 --python-version 3.11 --abi cp311 --only-binary=:all: && cp -r /project-src/aivideomaker /asset-output/python/aivideomaker",
                ],
                volumes=[
                    DockerVolume(
                        host_path=str(project_root / "src"),
                        container_path="/project-src",
                    )
                ],
            ),
        )

        function_bundling = lambda_python.BundlingOptions(
            image=lambda_.Runtime.PYTHON_3_11.bundling_image,
            command=[
                "bash",
                "-c",
                "mkdir -p /asset-output && "
                "cp -r /asset-input/. /asset-output && "
                "cp -r /project-src/aivideomaker /asset-output/aivideomaker",
            ],
            volumes=[
                DockerVolume(
                    host_path=str(project_root / "src"),
                    container_path="/project-src",
                )
            ],
        )

        ingest_bundling = lambda_python.BundlingOptions(
            image=lambda_.Runtime.PYTHON_3_11.bundling_image,
            command=[
                "bash",
                "-c",
                "mkdir -p /asset-output && cp -r /asset-input/. /asset-output && cp -r /project-src/aivideomaker /asset-output/aivideomaker && pip install --no-cache-dir pydantic --target /asset-output --implementation cp --platform manylinux2014_x86_64 --python-version 3.11 --abi cp311 --only-binary=:all:",
            ],
            volumes=[
                DockerVolume(
                    host_path=str(project_root / "src"),
                    container_path="/project-src",
                )
            ],
        )

        ingest_lambda = lambda_python.PythonFunction(
            self,
            "JobIngestLambda",
            entry=str(lambda_src),
            index="job_ingest/handler.py",
            handler="handler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "JOBS_TABLE_NAME": jobs_table.table_name,
                "STAGE": stage,
            },
            layers=[shared_layer],
            bundling=ingest_bundling,
        )
        jobs_table.grant_write_data(ingest_lambda)

        api = apigateway.RestApi(
            self,
            "VideoJobsApi",
            rest_api_name="Video Automation Jobs",
            api_key_source_type=apigateway.ApiKeySourceType.HEADER,
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_methods=["POST", "OPTIONS"],
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_headers=["*"],
            ),
        )
        jobs_resource = api.root.add_resource("jobs")
        jobs_integration = apigateway.LambdaIntegration(ingest_lambda)
        jobs_resource.add_method("POST", jobs_integration, api_key_required=True)

        api_key_value = self.node.try_get_context("jobsApiKey") or "".join(
            secrets.choice(string.ascii_letters + string.digits)
            for _ in range(40)
        )
        api_key_name = f"video-automation-{stage}-{self.node.addr[:8]}"
        api_key = apigateway.ApiKey(
            self,
            "VideoJobsApiKey",
            api_key_name=api_key_name,
            description="API key required to call the video jobs ingest endpoint",
            enabled=True,
            value=api_key_value,
        )
        usage_plan = api.add_usage_plan(
            "VideoJobsUsagePlan",
            name=f"video-automation-{stage}",
            throttle=apigateway.ThrottleSettings(rate_limit=10, burst_limit=2),
        )
        usage_plan.add_api_stage(api=api, stage=api.deployment_stage)
        usage_plan.add_api_key(api_key)

        scheduler_lambda = lambda_python.PythonFunction(
            self,
            "JobSchedulerLambda",
            entry=str(lambda_src),
            index="job_scheduler/handler.py",
            handler="handler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={
                "JOBS_TABLE_NAME": jobs_table.table_name,
                "STATUS_SCHEDULE_INDEX": self.STATUS_SCHEDULE_INDEX,
                "STAGE": stage,
            },
            layers=[shared_layer],
            bundling=function_bundling,
        )
        jobs_table.grant_read_write_data(scheduler_lambda)

        events.Rule(
            self,
            "JobDispatchRule",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(scheduler_lambda)],
        )

        worker_environment = {
            "JOBS_TABLE_NAME": jobs_table.table_name,
            "OUTPUT_BUCKET": output_bucket.bucket_name,
            "DATA_ROOT": "/tmp/data",
            "DEFAULT_DRY_RUN": "false",
            "FINAL_VIDEO_PREFIX": "jobs/final",
            "STAGE": stage,
        }

        secret_parameters: list[tuple[str, ssm.IParameter, str]] = []

        veo_credentials_param_name = self.node.try_get_context("veoCredentialsParameterName") or "/auto-sora/veo-service-account"
        veo_credentials_param = ssm.StringParameter.from_secure_string_parameter_attributes(
            self,
            "VeoCredentialsParameter",
            parameter_name=veo_credentials_param_name,
        )
        secret_parameters.append(("VEO_CREDENTIALS_PARAMETER", veo_credentials_param, veo_credentials_param_name))

        anthropic_param_name = self.node.try_get_context("anthropicApiKeyParameterName") or "/auto-sora/env/ANTHROPIC_API_KEY"
        anthropic_param = ssm.StringParameter.from_secure_string_parameter_attributes(
            self,
            "AnthropicKeyParameter",
            parameter_name=anthropic_param_name,
        )
        secret_parameters.append(("ANTHROPIC_API_KEY_PARAMETER", anthropic_param, anthropic_param_name))

        additional_secret_params = [
            ("openaiApiKeyParameterName", "OPENAI_API_KEY_PARAMETER", "/auto-sora/env/OPENAI_API_KEY"),
            ("elevenLabsApiKeyParameterName", "ELEVEN_LABS_API_KEY_PARAMETER", "/auto-sora/env/ELEVEN_LABS_API_KEY"),
            ("googleApiKeyParameterName", "GOOGLE_API_KEY_PARAMETER", "/auto-sora/env/GOOGLE_API_KEY"),
        ]

        for context_key, env_var, default_path in additional_secret_params:
            param_name = self.node.try_get_context(context_key) or default_path
            secret_param = ssm.StringParameter.from_secure_string_parameter_attributes(
                self,
                f"{env_var}Parameter",
                parameter_name=param_name,
            )
            secret_parameters.append((env_var, secret_param, param_name))

        worker_image_code = lambda_.DockerImageCode.from_image_asset(
            directory=str(project_root),
            file="backend/lambda_src/job_worker/Dockerfile",
            exclude=[
                "backend/cdk.out",
                "cdk.out",
                ".git",
                ".venv",
                "node_modules",
                "__pycache__",
            ],
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        worker_lambda = lambda_.DockerImageFunction(
            self,
            "JobWorkerLambda",
            code=worker_image_code,
            timeout=Duration.minutes(15),
            memory_size=4096,
            environment=worker_environment,
        )
        jobs_table.grant_read_write_data(worker_lambda)
        output_bucket.grant_read_write(worker_lambda)
        for env_var, parameter, name in secret_parameters:
            worker_lambda.add_environment(env_var, name)
            parameter.grant_read(worker_lambda)

        failure_handler = tasks.LambdaInvoke(
            self,
            "MarkJobFailed",
            lambda_function=worker_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "action": "MARK_FAILED",
                    "jobContext.$": "$.jobContext",
                    "job.$": "$.job",
                    "error.$": "$.error",
                }
            ),
            result_path=sfn.JsonPath.DISCARD,
            payload_response_only=True,
        )
        fail_state = sfn.Fail(self, "JobFailed")
        failure_chain = sfn.Chain.start(failure_handler).next(fail_state)

        initialize_state = sfn.Pass(
            self,
            "InitializeState",
            parameters={"job.$": "$"},
        )

        generate_prompts_task = tasks.LambdaInvoke(
            self,
            "GeneratePrompts",
            lambda_function=worker_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "action": "GENERATE_PROMPTS",
                    "job.$": "$.job",
                }
            ),
            result_path="$.jobContext",
            payload_response_only=True,
        )
        generate_prompts_task.add_catch(failure_chain, result_path="$.error")

        render_clips_map = sfn.Map(
            self,
            "RenderClips",
            items_path="$.jobContext.clipIds",
            parameters={
                "jobContext.$": "$.jobContext",
                "clipId.$": "$$.Map.Item.Value",
            },
            result_path=sfn.JsonPath.DISCARD,
        )
        render_clips_map.iterator(
            tasks.LambdaInvoke(
                self,
                "RenderClip",
                lambda_function=worker_lambda,
                payload=sfn.TaskInput.from_object(
                    {
                        "action": "GENERATE_CLIP",
                        "jobContext.$": "$.jobContext",
                        "clipId.$": "$.clipId",
                    }
                ),
                result_path=sfn.JsonPath.DISCARD,
                payload_response_only=True,
            )
        )
        render_clips_map.add_catch(failure_chain, result_path="$.error")

        stitch_task = tasks.LambdaInvoke(
            self,
            "StitchFinalVideo",
            lambda_function=worker_lambda,
            payload=sfn.TaskInput.from_object(
                {
                    "action": "STITCH_FINAL",
                    "jobContext.$": "$.jobContext",
                }
            ),
            result_path="$.stitchResult",
            payload_response_only=True,
        )
        stitch_task.add_catch(failure_chain, result_path="$.error")

        workflow_definition = initialize_state.next(generate_prompts_task).next(render_clips_map).next(stitch_task)
        state_machine = sfn.StateMachine(
            self,
            "VideoJobStateMachine",
            definition=workflow_definition,
            timeout=Duration.hours(2),
        )

        scheduler_lambda.add_environment("STATE_MACHINE_ARN", state_machine.state_machine_arn)
        state_machine.grant_start_execution(scheduler_lambda)

        gdrive_lambda = lambda_python.PythonFunction(
            self,
            "GoogleDriveForwarderLambda",
            entry=str(lambda_src),
            index="gdrive_forwarder/handler.py",
            handler="handler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            timeout=Duration.minutes(2),
            memory_size=512,
            environment={
                "GDRIVE_SECRET_NAME": google_credentials_secret.secret_name,
                "GDRIVE_FOLDER_ID": "REPLACE_ME",
                "STAGE": stage,
            },
            layers=[shared_layer],
            bundling=lambda_python.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_11.bundling_image,
                command=[
                    "bash",
                    "-c",
                    "mkdir -p /asset-output && cp -r /asset-input/. /asset-output && pip install --no-cache-dir -r gdrive_forwarder/requirements.txt --target /asset-output --implementation cp --platform manylinux2014_x86_64 --python-version 3.11 --abi cp311 --only-binary=:all: && cp -r /project-src/aivideomaker /asset-output/aivideomaker",
                ],
                volumes=[
                    DockerVolume(
                        host_path=str(project_root / "src"),
                        container_path="/project-src",
                    )
                ],
            ),
        )
        output_bucket.grant_read(gdrive_lambda)
        google_credentials_secret.grant_read(gdrive_lambda)

        gdrive_lambda.add_event_source(
            lambda_event_sources.S3EventSource(
                output_bucket,
                events=[s3.EventType.OBJECT_CREATED],
                filters=[s3.NotificationKeyFilter(prefix="jobs/final/", suffix=".mp4")],
            )
        )

        CfnOutput(
            self,
            "VideoJobsApiKeyOutput",
            value=api_key_value,
            description="API key value required by the video jobs ingest endpoint",
        )
