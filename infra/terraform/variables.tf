variable "aws_region" {
  type        = string
  description = "AWS deployment region"
  default     = "ap-south-1"
}

variable "project_name" {
  type        = string
  description = "Unique project identifier used for naming resources"
  default     = "mcp-control-plane"
}

variable "environment" {
  type        = string
  description = "Deployment environment (prod/staging)"
  default     = "prod"
}

variable "image_tag" {
  type        = string
  description = "Container image tag deployed from ECR"
  default     = "latest"
}

variable "allowed_origins" {
  type        = list(string)
  description = "Allowed origins for MCP gateway DNS rebinding defense"
  default     = ["http://localhost:3000", "https://app.example.com"]
}
