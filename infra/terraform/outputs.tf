output "ecr_repository_url" {
  description = "URL of the created ECR container repository"
  value       = aws_ecr_repository.gateway.repository_url
}

output "api_gateway_endpoint" {
  description = "Public HTTPS invocation URL of the API Gateway HTTP API"
  value       = aws_apigatewayv2_stage.default_stage.invoke_url
}

output "lambda_function_name" {
  description = "Name of the deployed Lambda container function"
  value       = aws_lambda_function.gateway.function_name
}
