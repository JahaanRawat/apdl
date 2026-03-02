variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "project" {
  description = "Project name"
  type        = string
}

variable "cluster_version" {
  description = "Kubernetes version"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID to deploy into"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for EKS"
  type        = list(string)
}

variable "node_instance_types" {
  description = "EC2 instance types for the managed node group"
  type        = list(string)
}

variable "min_nodes" {
  description = "Minimum node count"
  type        = number
}

variable "max_nodes" {
  description = "Maximum node count"
  type        = number
}

variable "desired_nodes" {
  description = "Desired node count"
  type        = number
}
