output "sns_topic_arn" {
  description = "ARN of the SNS topic for monitoring alerts"
  value       = aws_sns_topic.alerts.arn
}

output "sns_topic_name" {
  description = "Name of the SNS topic for monitoring alerts"
  value       = aws_sns_topic.alerts.name
}

output "cloudwatch_log_groups" {
  description = "Map of service names to CloudWatch log group names"
  value = {
    ingestion  = aws_cloudwatch_log_group.ingestion.name
    config     = aws_cloudwatch_log_group.config.name
    query      = aws_cloudwatch_log_group.query.name
    agents     = aws_cloudwatch_log_group.agents.name
    clickhouse = aws_cloudwatch_log_group.clickhouse.name
  }
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}
