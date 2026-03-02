variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "project" {
  description = "Project name"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "eks_cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "eks_oidc_issuer" {
  description = "OIDC issuer URL for the EKS cluster (without https://)"
  type        = string
}

variable "alert_email" {
  description = "Email address for alert notifications"
  type        = string
}

variable "redis_cluster_id" {
  description = "ElastiCache Redis replication group ID for alarms"
  type        = string
}

variable "rds_instance_id" {
  description = "RDS instance identifier for alarms"
  type        = string
}

variable "clickhouse_asg_name" {
  description = "ClickHouse Auto Scaling Group name for alarms"
  type        = string
}
