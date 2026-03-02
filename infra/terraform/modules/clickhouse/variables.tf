variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "project" {
  description = "Project name"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for ClickHouse instances"
  type        = list(string)
}

variable "instance_type" {
  description = "EC2 instance type for ClickHouse nodes"
  type        = string
}

variable "volume_size" {
  description = "EBS volume size in GB for ClickHouse data"
  type        = number
}

variable "node_count" {
  description = "Number of ClickHouse nodes"
  type        = number
}

variable "allowed_security_group_ids" {
  description = "Security group IDs allowed to connect to ClickHouse"
  type        = list(string)
}
