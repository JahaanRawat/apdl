output "db_instance_id" {
  description = "Identifier of the RDS instance"
  value       = aws_db_instance.postgres.identifier
}

output "db_endpoint" {
  description = "Connection endpoint for the RDS instance"
  value       = aws_db_instance.postgres.endpoint
}

output "db_address" {
  description = "Hostname of the RDS instance (without port)"
  value       = aws_db_instance.postgres.address
}

output "db_port" {
  description = "Port of the RDS instance"
  value       = aws_db_instance.postgres.port
}

output "db_name" {
  description = "Name of the default database"
  value       = aws_db_instance.postgres.db_name
}

output "security_group_id" {
  description = "Security group ID for PostgreSQL"
  value       = aws_security_group.postgres.id
}

output "credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret containing database credentials"
  value       = aws_secretsmanager_secret.postgres_credentials.arn
}

output "kms_key_arn" {
  description = "ARN of the KMS key used for encryption"
  value       = aws_kms_key.postgres.arn
}
