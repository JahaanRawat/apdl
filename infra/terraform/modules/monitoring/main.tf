###############################################################################
# Locals
###############################################################################

locals {
  name_prefix = "${var.project}-${var.environment}"
}

###############################################################################
# SNS Topic for Alerts
###############################################################################

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"

  tags = {
    Name = "${local.name_prefix}-alerts"
  }
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alert_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

###############################################################################
# CloudWatch Log Groups
###############################################################################

resource "aws_cloudwatch_log_group" "ingestion" {
  name              = "/${var.project}/${var.environment}/ingestion"
  retention_in_days = var.environment == "prod" ? 90 : 30

  tags = {
    Name    = "${local.name_prefix}-ingestion-logs"
    Service = "ingestion"
  }
}

resource "aws_cloudwatch_log_group" "config" {
  name              = "/${var.project}/${var.environment}/config"
  retention_in_days = var.environment == "prod" ? 90 : 30

  tags = {
    Name    = "${local.name_prefix}-config-logs"
    Service = "config"
  }
}

resource "aws_cloudwatch_log_group" "query" {
  name              = "/${var.project}/${var.environment}/query"
  retention_in_days = var.environment == "prod" ? 90 : 30

  tags = {
    Name    = "${local.name_prefix}-query-logs"
    Service = "query"
  }
}

resource "aws_cloudwatch_log_group" "agents" {
  name              = "/${var.project}/${var.environment}/agents"
  retention_in_days = var.environment == "prod" ? 90 : 30

  tags = {
    Name    = "${local.name_prefix}-agents-logs"
    Service = "agents"
  }
}

resource "aws_cloudwatch_log_group" "clickhouse" {
  name              = "/${var.project}/${var.environment}/clickhouse"
  retention_in_days = var.environment == "prod" ? 90 : 30

  tags = {
    Name    = "${local.name_prefix}-clickhouse-logs"
    Service = "clickhouse"
  }
}

###############################################################################
# CloudWatch Alarms -- RDS PostgreSQL
###############################################################################

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name_prefix}-rds-cpu-high"
  alarm_description   = "RDS CPU utilization exceeds 80% for 5 minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${local.name_prefix}-rds-cpu-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_free_storage" {
  alarm_name          = "${local.name_prefix}-rds-storage-low"
  alarm_description   = "RDS free storage space below 10 GB"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 10737418240 # 10 GB in bytes

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${local.name_prefix}-rds-storage-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_free_memory" {
  alarm_name          = "${local.name_prefix}-rds-memory-low"
  alarm_description   = "RDS freeable memory below 256 MB"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  metric_name         = "FreeableMemory"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 268435456 # 256 MB in bytes

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${local.name_prefix}-rds-memory-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  alarm_name          = "${local.name_prefix}-rds-connections-high"
  alarm_description   = "RDS database connections exceed 150"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 150

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${local.name_prefix}-rds-connections-alarm"
  }
}

###############################################################################
# CloudWatch Alarms -- ElastiCache Redis
###############################################################################

resource "aws_cloudwatch_metric_alarm" "redis_cpu" {
  alarm_name          = "${local.name_prefix}-redis-cpu-high"
  alarm_description   = "Redis CPU utilization exceeds 75% for 5 minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 75

  dimensions = {
    ReplicationGroupId = var.redis_cluster_id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${local.name_prefix}-redis-cpu-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${local.name_prefix}-redis-memory-high"
  alarm_description   = "Redis memory usage exceeds 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "DatabaseMemoryUsagePercentage"
  namespace           = "AWS/ElastiCache"
  period              = 300
  statistic           = "Average"
  threshold           = 80

  dimensions = {
    ReplicationGroupId = var.redis_cluster_id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${local.name_prefix}-redis-memory-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_evictions" {
  alarm_name          = "${local.name_prefix}-redis-evictions"
  alarm_description   = "Redis evictions exceed 100 per minute"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Evictions"
  namespace           = "AWS/ElastiCache"
  period              = 60
  statistic           = "Sum"
  threshold           = 100

  dimensions = {
    ReplicationGroupId = var.redis_cluster_id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Name = "${local.name_prefix}-redis-evictions-alarm"
  }
}

###############################################################################
# CloudWatch Dashboard
###############################################################################

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name_prefix}-overview"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 1
        properties = {
          markdown = "# APDL ${upper(var.environment)} Infrastructure Dashboard"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 1
        width  = 8
        height = 6
        properties = {
          title   = "RDS CPU Utilization"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.rds_instance_id]
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 1
        width  = 8
        height = 6
        properties = {
          title   = "RDS Free Storage Space"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", var.rds_instance_id]
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 1
        width  = 8
        height = 6
        properties = {
          title   = "RDS Database Connections"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", var.rds_instance_id]
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 7
        width  = 8
        height = 6
        properties = {
          title   = "Redis CPU Utilization"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/ElastiCache", "CPUUtilization", "ReplicationGroupId", var.redis_cluster_id]
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 7
        width  = 8
        height = 6
        properties = {
          title   = "Redis Memory Usage"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/ElastiCache", "DatabaseMemoryUsagePercentage", "ReplicationGroupId", var.redis_cluster_id]
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 7
        width  = 8
        height = 6
        properties = {
          title   = "Redis Cache Hits vs Misses"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/ElastiCache", "CacheHits", "ReplicationGroupId", var.redis_cluster_id],
            ["AWS/ElastiCache", "CacheMisses", "ReplicationGroupId", var.redis_cluster_id]
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 13
        width  = 12
        height = 6
        properties = {
          title   = "ClickHouse ASG - Instance Count"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/AutoScaling", "GroupInServiceInstances", "AutoScalingGroupName", var.clickhouse_asg_name]
          ]
          period = 300
          region = var.aws_region
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 13
        width  = 12
        height = 6
        properties = {
          title   = "EKS Node Group CPU"
          view    = "timeSeries"
          stacked = false
          metrics = [
            ["AWS/EKS", "node_cpu_utilization", "ClusterName", var.eks_cluster_name]
          ]
          period = 300
          region = var.aws_region
        }
      }
    ]
  })
}

###############################################################################
# Helm: kube-prometheus-stack (Prometheus + Grafana)
###############################################################################

resource "helm_release" "kube_prometheus_stack" {
  name             = "kube-prometheus-stack"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = "58.2.2"
  namespace        = "monitoring"
  create_namespace = true
  wait             = true
  timeout          = 600

  values = [
    yamlencode({
      prometheus = {
        prometheusSpec = {
          retention         = var.environment == "prod" ? "30d" : "7d"
          retentionSize     = var.environment == "prod" ? "45GB" : "15GB"
          replicas          = var.environment == "prod" ? 2 : 1
          resources = {
            requests = {
              cpu    = var.environment == "prod" ? "500m" : "200m"
              memory = var.environment == "prod" ? "2Gi" : "512Mi"
            }
            limits = {
              cpu    = var.environment == "prod" ? "2" : "500m"
              memory = var.environment == "prod" ? "4Gi" : "1Gi"
            }
          }
          storageSpec = {
            volumeClaimTemplate = {
              spec = {
                storageClassName = "gp3"
                accessModes      = ["ReadWriteOnce"]
                resources = {
                  requests = {
                    storage = var.environment == "prod" ? "50Gi" : "20Gi"
                  }
                }
              }
            }
          }
          serviceMonitorSelectorNilUsesHelmValues = false
          podMonitorSelectorNilUsesHelmValues     = false
        }
      }

      grafana = {
        enabled = true
        adminPassword = "changeme-on-first-login"
        persistence = {
          enabled          = true
          size             = "10Gi"
          storageClassName = "gp3"
        }
        resources = {
          requests = {
            cpu    = "100m"
            memory = "256Mi"
          }
          limits = {
            cpu    = "500m"
            memory = "512Mi"
          }
        }
        "grafana.ini" = {
          server = {
            root_url = "%(protocol)s://%(domain)s:%(http_port)s/grafana/"
          }
          auth = {
            disable_login_form = false
          }
        }
        dashboardProviders = {
          "dashboardproviders.yaml" = {
            apiVersion = 1
            providers = [
              {
                name            = "default"
                orgId           = 1
                folder          = ""
                type            = "file"
                disableDeletion = false
                editable        = true
                options = {
                  path = "/var/lib/grafana/dashboards/default"
                }
              }
            ]
          }
        }
      }

      alertmanager = {
        enabled = true
        alertmanagerSpec = {
          replicas = var.environment == "prod" ? 2 : 1
          resources = {
            requests = {
              cpu    = "50m"
              memory = "64Mi"
            }
            limits = {
              cpu    = "200m"
              memory = "128Mi"
            }
          }
        }
      }

      kubeStateMetrics = {
        enabled = true
      }

      nodeExporter = {
        enabled = true
      }

      defaultRules = {
        create = true
        rules = {
          alertmanager                = true
          etcd                       = true
          configReloaders            = true
          general                    = true
          k8s                        = true
          kubeApiserverAvailability  = true
          kubeApiserverBurnrate      = true
          kubeApiserverHistogram     = true
          kubeApiserverSlos          = true
          kubeControllerManager      = true
          kubelet                    = true
          kubeProxy                  = true
          kubePrometheusGeneral      = true
          kubePrometheusNodeRecording = true
          kubernetesApps             = true
          kubernetesResources        = true
          kubernetesStorage          = true
          kubernetesSystem           = true
          kubeSchedulerAlerting      = true
          kubeSchedulerRecording     = true
          kubeStateMetrics           = true
          network                    = true
          node                       = true
          nodeExporterAlerting       = true
          nodeExporterRecording      = true
          prometheus                 = true
          prometheusOperator         = true
        }
      }
    })
  ]
}
