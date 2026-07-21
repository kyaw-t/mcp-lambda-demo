#!/usr/bin/env bash
#
# Package the tool code and create (or update) the Lambda function, plus the
# IAM execution role the function runs as.
#
# Prerequisites:
#   * AWS CLI v2 configured with credentials for the target account.
#   * `source deploy/config.sh` has been run (see config.example.sh).
#
# Idempotent: re-running updates the function code in place.

set -euo pipefail

: "${AWS_REGION:?source deploy/config.sh first}"
: "${ACCOUNT_ID:?source deploy/config.sh first}"
: "${LAMBDA_FUNCTION_NAME:?}"
: "${LAMBDA_ROLE_NAME:?}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BUILD_DIR="$(mktemp -d)"
ZIP_PATH="$BUILD_DIR/function.zip"

echo "==> Packaging Lambda from src/lambda/ ..."
# The demo has no third-party dependencies, so packaging is just zipping the
# source. If you add dependencies, `pip install -r requirements.txt -t <dir>`
# into the build dir before zipping.
( cd "$ROOT/src/lambda" && zip -qr "$ZIP_PATH" . -x '*__pycache__*' )
echo "    built $ZIP_PATH"

# ---- Execution role -------------------------------------------------------
LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${LAMBDA_ROLE_NAME}"
if aws iam get-role --role-name "$LAMBDA_ROLE_NAME" >/dev/null 2>&1; then
  echo "==> Lambda execution role already exists: $LAMBDA_ROLE_NAME"
else
  echo "==> Creating Lambda execution role: $LAMBDA_ROLE_NAME"
  aws iam create-role \
    --role-name "$LAMBDA_ROLE_NAME" \
    --assume-role-policy-document "file://$ROOT/deploy/iam/lambda-trust-policy.json" \
    >/dev/null
  aws iam attach-role-policy \
    --role-name "$LAMBDA_ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  echo "    waiting ~10s for IAM role to propagate ..."
  sleep 10
fi

# ---- Function -------------------------------------------------------------
if aws lambda get-function --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "==> Updating existing function code: $LAMBDA_FUNCTION_NAME"
  aws lambda update-function-code \
    --function-name "$LAMBDA_FUNCTION_NAME" \
    --zip-file "fileb://$ZIP_PATH" \
    --region "$AWS_REGION" \
    --no-cli-pager >/dev/null
else
  echo "==> Creating function: $LAMBDA_FUNCTION_NAME"
  aws lambda create-function \
    --function-name "$LAMBDA_FUNCTION_NAME" \
    --runtime "python3.12" \
    --role "$LAMBDA_ROLE_ARN" \
    --handler "handler.lambda_handler" \
    --timeout 30 \
    --memory-size 256 \
    --zip-file "fileb://$ZIP_PATH" \
    --region "$AWS_REGION" \
    --no-cli-pager >/dev/null
fi

aws lambda wait function-updated \
  --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION"

LAMBDA_ARN="arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${LAMBDA_FUNCTION_NAME}"
rm -rf "$BUILD_DIR"

echo ""
echo "Done. Lambda ARN:"
echo "    $LAMBDA_ARN"
echo ""
echo "Next: run  python deploy/20_setup_cognito.py"
