# Single container image for both Lambda functions (issue #139):
#   lifecycle-pipeline  -> lambda_pipeline.handler (durable)
#   lifecycle-api       -> lambda_api.handler      (plain)
# The CDK sets the handler per function via image CMD override.
FROM public.ecr.aws/lambda/python:3.13

# Install dependencies (separate layer for caching)
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Application code
COPY . ${LAMBDA_TASK_ROOT}/

# Default handler; overridden per function in CDK
CMD ["lambda_api.handler"]
