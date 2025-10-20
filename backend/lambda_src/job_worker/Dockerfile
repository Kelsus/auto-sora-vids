FROM public.ecr.aws/lambda/python:3.11

WORKDIR /var/task

# Install system dependencies required by moviepy/ffmpeg if needed.
RUN yum install -y mesa-libGL && yum clean all

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

COPY infra/lambda_src/job_worker ./job_worker
COPY infra/lambda_src/common_layer/python/common ./common

CMD ["job_worker.handler"]
