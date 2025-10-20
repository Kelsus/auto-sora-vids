#!/usr/bin/env python3
import os
import re
from aws_cdk import App, Environment

from pipeline_stack import VideoAutomationStack


app = App()

stage = app.node.try_get_context("stage") or os.getenv("CDK_STAGE") or "dev"
stage_slug = re.sub(r"[^A-Za-z0-9-]", "-", stage).strip("-") or "dev"

VideoAutomationStack(
    app,
    f"VideoAutomationStack-{stage_slug}",
    stage=stage_slug,
    env=Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region"),
    ),
)

app.synth()
