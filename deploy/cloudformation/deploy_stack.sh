#!/usr/bin/env bash
#
# One-command deploy of the whole AgentCore harness via CloudFormation.
#
# It packages the Lambda from src/lambda/, uploads the zip to S3, then creates
# (or updates) the CloudFormation stack that provisions everything else:
# Lambda, both IAM roles, Cognito, the gateway, and the Lambda target.
#
# Usage:
#   export AWS_REGION=us-east-1
#   export CODE_BUCKET=my-existing-bucket          # an S3 bucket in AWS_REGION
#   export COGNITO_DOMAIN_PREFIX=mcp-orders-123456789012   # globally unique
#   ./deploy/cloudformation/deploy_stack.sh
#
# Prereqs: AWS CLI v2 configured; an existing S3 bucket you own.

set -euo pipefail

: "${AWS_REGION:?set AWS_REGION}"
: "${CODE_BUCKET:?set CODE_BUCKET to an existing S3 bucket in the region}"
: "${COGNITO_DOMAIN_PREFIX:?set COGNITO_DOMAIN_PREFIX (globally unique)}"

STACK_NAME="${STACK_NAME:-agentcore-harness}"
CODE_KEY="${CODE_KEY:-agentcore/function.zip}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
TEMPLATE="$HERE/agentcore-harness.yaml"
BUILD_DIR="$(mktemp -d)"
ZIP_PATH="$BUILD_DIR/function.zip"

echo "==> Packaging Lambda from src/lambda/ ..."
( cd "$ROOT/src/lambda" && zip -qr "$ZIP_PATH" . -x '*__pycache__*' )

echo "==> Uploading package to s3://${CODE_BUCKET}/${CODE_KEY} ..."
aws s3 cp "$ZIP_PATH" "s3://${CODE_BUCKET}/${CODE_KEY}" --region "$AWS_REGION"

echo "==> Deploying stack '${STACK_NAME}' ..."
aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$TEMPLATE" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides \
    LambdaCodeS3Bucket="$CODE_BUCKET" \
    LambdaCodeS3Key="$CODE_KEY" \
    CognitoDomainPrefix="$COGNITO_DOMAIN_PREFIX"

rm -rf "$BUILD_DIR"

echo ""
echo "==> Stack outputs:"
aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[].{Key:OutputKey,Value:OutputValue}" \
  --output table

echo ""
echo "Grab the client secret (not shown above) with the ClientSecretRetrievalCommand output,"
echo "then paste all values into deploy/config.sh and run: python client/test_client.py"
