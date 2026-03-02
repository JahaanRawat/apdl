output "nlb_dns_name" {
  description = "DNS name of the ClickHouse Network Load Balancer"
  value       = aws_lb.clickhouse.dns_name
}

output "nlb_arn" {
  description = "ARN of the ClickHouse Network Load Balancer"
  value       = aws_lb.clickhouse.arn
}

output "security_group_id" {
  description = "Security group ID for ClickHouse instances"
  value       = aws_security_group.clickhouse.id
}

output "asg_name" {
  description = "Name of the ClickHouse Auto Scaling Group"
  value       = aws_autoscaling_group.clickhouse.name
}

output "backup_bucket_name" {
  description = "S3 bucket name for ClickHouse backups"
  value       = aws_s3_bucket.clickhouse_backups.id
}

output "instance_profile_arn" {
  description = "ARN of the IAM instance profile for ClickHouse"
  value       = aws_iam_instance_profile.clickhouse.arn
}
