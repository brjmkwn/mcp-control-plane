provider "aws" {
  region = var.aws_region
}

# 1. ECR Repository for Gateway Container Images
resource "aws_ecr_repository" "gateway" {
  name                 = "${var.project_name}-gateway"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# 2. CloudWatch Log Group with 14-day retention
resource "aws_cloudwatch_log_group" "gateway_logs" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-gateway"
  retention_in_days = 14

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# 3. Least-Privilege IAM Execution Role for Lambda
resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-${var.environment}-lambda-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "lambda_policy" {
  name        = "${var.project_name}-${var.environment}-policy"
  description = "Scoped permissions for CloudWatch Logs and ECR pull"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.gateway_logs.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability"
        ]
        Resource = aws_ecr_repository.gateway.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

# 4. AWS Lambda Container Function (Mangum ASGI Adapter)
resource "aws_lambda_function" "gateway" {
  function_name = "${var.project_name}-${var.environment}-gateway"
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.gateway.repository_url}:${var.image_tag}"
  timeout       = 30
  memory_size   = 512

  environment {
    variables = {
      MCP_ALLOWED_ORIGINS = join(",", var.allowed_origins)
      MCP_TEST_MODE       = "false"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_attach,
    aws_cloudwatch_log_group.gateway_logs
  ]

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# 5. Amazon API Gateway v2 (HTTP API)
resource "aws_apigatewayv2_api" "http_api" {
  name          = "${var.project_name}-${var.environment}-http-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.allowed_origins
    allow_methods = ["POST", "GET", "OPTIONS"]
    allow_headers = ["Authorization", "Content-Type", "Accept", "Origin"]
    max_age       = 300
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# Integration with Lambda
resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.gateway.arn
  payload_format_version = "2.0"
}

# Routes: POST /mcp and GET /health
resource "aws_apigatewayv2_route" "mcp_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /mcp"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

resource "aws_apigatewayv2_route" "health_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

# Stage with auto-deploy
resource "aws_apigatewayv2_stage" "default_stage" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default"
  auto_deploy = true

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# Permission for API Gateway to invoke Lambda
resource "aws_lambda_permission" "apigw_lambda" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gateway.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}
