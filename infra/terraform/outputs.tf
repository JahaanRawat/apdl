###############################################################################
# Networking
###############################################################################

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.networking.vpc_id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = module.networking.private_subnet_ids
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = module.networking.public_subnet_ids
}

###############################################################################
# EKS
###############################################################################

output "eks_cluster_name" {
  description = "Name of the EKS cluster"
  value       = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  description = "Endpoint URL of the EKS cluster API server"
  value       = module.eks.cluster_endpoint
}

output "eks_kubeconfig_command" {
  description = "AWS CLI command to update kubeconfig for cluster access"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "eks_oidc_issuer" {
  description = "OIDC issuer URL for IRSA"
  value       = module.eks.oidc_issuer
}

###############################################################################
# Redis
###############################################################################

output "redis_primary_endpoint" {
  description = "Primary endpoint for the Redis replication group"
  value       = module.redis.primary_endpoint
}

output "redis_reader_endpoint" {
  description = "Reader endpoint for the Redis replication group"
  value       = module.redis.reader_endpoint
}

output "redis_port" {
  description = "Port number for Redis connections"
  value       = module.redis.port
}

###############################################################################
# ClickHouse
###############################################################################

output "clickhouse_nlb_dns" {
  description = "DNS name of the ClickHouse Network Load Balancer"
  value       = module.clickhouse.nlb_dns_name
}

output "clickhouse_http_port" {
  description = "HTTP port for ClickHouse connections"
  value       = 8123
}

output "clickhouse_native_port" {
  description = "Native protocol port for ClickHouse connections"
  value       = 9000
}

###############################################################################
# PostgreSQL
###############################################################################

output "postgres_endpoint" {
  description = "Connection endpoint for the PostgreSQL RDS instance"
  value       = module.postgres.db_endpoint
}

output "postgres_port" {
  description = "Port number for PostgreSQL connections"
  value       = module.postgres.db_port
}

output "postgres_database_name" {
  description = "Name of the default PostgreSQL database"
  value       = var.postgres_database_name
}

###############################################################################
# Monitoring
###############################################################################

output "grafana_note" {
  description = "Instructions for accessing Grafana"
  value       = "Grafana is deployed via Helm into the EKS cluster. Port-forward with: kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80"
}

output "sns_alert_topic_arn" {
  description = "ARN of the SNS topic used for monitoring alerts"
  value       = module.monitoring.sns_topic_arn
}
