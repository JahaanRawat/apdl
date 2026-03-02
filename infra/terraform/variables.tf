###############################################################################
# General
###############################################################################

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

variable "project_name" {
  description = "Project name used for resource naming and tagging"
  type        = string
  default     = "apdl"
}

###############################################################################
# Networking
###############################################################################

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

###############################################################################
# EKS
###############################################################################

variable "eks_cluster_version" {
  description = "Kubernetes version for the EKS cluster"
  type        = string
  default     = "1.29"
}

variable "eks_node_instance_types" {
  description = "EC2 instance types for the EKS managed node group"
  type        = list(string)
  default     = ["t3.large"]
}

variable "eks_min_nodes" {
  description = "Minimum number of nodes in the EKS node group"
  type        = number
  default     = 2
}

variable "eks_max_nodes" {
  description = "Maximum number of nodes in the EKS node group"
  type        = number
  default     = 6
}

variable "eks_desired_nodes" {
  description = "Desired number of nodes in the EKS node group"
  type        = number
  default     = 3
}

###############################################################################
# Redis (ElastiCache)
###############################################################################

variable "redis_node_type" {
  description = "ElastiCache node type for Redis"
  type        = string
  default     = "cache.t3.medium"
}

variable "redis_num_cache_nodes" {
  description = "Number of cache nodes in the Redis replication group (includes primary)"
  type        = number
  default     = 2
}

###############################################################################
# ClickHouse
###############################################################################

variable "clickhouse_instance_type" {
  description = "EC2 instance type for ClickHouse nodes"
  type        = string
  default     = "r6i.xlarge"
}

variable "clickhouse_volume_size" {
  description = "EBS volume size in GB for ClickHouse data"
  type        = number
  default     = 500
}

variable "clickhouse_node_count" {
  description = "Number of ClickHouse nodes"
  type        = number
  default     = 1
}

###############################################################################
# PostgreSQL (RDS)
###############################################################################

variable "postgres_instance_class" {
  description = "RDS instance class for PostgreSQL"
  type        = string
  default     = "db.t3.medium"
}

variable "postgres_allocated_storage" {
  description = "Allocated storage in GB for PostgreSQL"
  type        = number
  default     = 100
}

variable "postgres_engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "16.2"
}

variable "postgres_database_name" {
  description = "Name of the default database to create"
  type        = string
  default     = "apdl"
}

###############################################################################
# Monitoring
###############################################################################

variable "alert_email" {
  description = "Email address for monitoring alerts"
  type        = string
  default     = ""
}

###############################################################################
# DNS (optional)
###############################################################################

variable "domain_name" {
  description = "Domain name for Route53 hosted zone (leave empty to skip DNS setup)"
  type        = string
  default     = ""
}
