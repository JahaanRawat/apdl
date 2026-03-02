###############################################################################
# Locals
###############################################################################

locals {
  name_prefix = "${var.project}-${var.environment}"
  # Use provisioned IOPS in prod for consistent performance
  volume_type = var.environment == "prod" ? "gp3" : "gp3"
  volume_iops = var.environment == "prod" ? 10000 : 3000
  volume_throughput = var.environment == "prod" ? 500 : 125
}

###############################################################################
# Data Sources
###############################################################################

data "aws_region" "current" {}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

###############################################################################
# IAM Instance Profile (for S3 backups and SSM access)
###############################################################################

data "aws_iam_policy_document" "clickhouse_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "clickhouse" {
  name               = "${local.name_prefix}-clickhouse-role"
  assume_role_policy = data.aws_iam_policy_document.clickhouse_assume.json

  tags = {
    Name = "${local.name_prefix}-clickhouse-role"
  }
}

data "aws_iam_policy_document" "clickhouse_s3" {
  statement {
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      "s3:DeleteObject",
    ]

    resources = [
      "arn:aws:s3:::${local.name_prefix}-clickhouse-backups",
      "arn:aws:s3:::${local.name_prefix}-clickhouse-backups/*",
    ]
  }
}

resource "aws_iam_role_policy" "clickhouse_s3" {
  name   = "${local.name_prefix}-clickhouse-s3"
  role   = aws_iam_role.clickhouse.id
  policy = data.aws_iam_policy_document.clickhouse_s3.json
}

resource "aws_iam_role_policy_attachment" "clickhouse_ssm" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  role       = aws_iam_role.clickhouse.name
}

resource "aws_iam_instance_profile" "clickhouse" {
  name = "${local.name_prefix}-clickhouse-profile"
  role = aws_iam_role.clickhouse.name

  tags = {
    Name = "${local.name_prefix}-clickhouse-profile"
  }
}

###############################################################################
# S3 Bucket for Backups
###############################################################################

resource "aws_s3_bucket" "clickhouse_backups" {
  bucket = "${local.name_prefix}-clickhouse-backups"

  tags = {
    Name = "${local.name_prefix}-clickhouse-backups"
  }
}

resource "aws_s3_bucket_versioning" "clickhouse_backups" {
  bucket = aws_s3_bucket.clickhouse_backups.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "clickhouse_backups" {
  bucket = aws_s3_bucket.clickhouse_backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "clickhouse_backups" {
  bucket = aws_s3_bucket.clickhouse_backups.id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"

    expiration {
      days = var.environment == "prod" ? 90 : 30
    }

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }
}

resource "aws_s3_bucket_public_access_block" "clickhouse_backups" {
  bucket = aws_s3_bucket.clickhouse_backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

###############################################################################
# Security Group
###############################################################################

resource "aws_security_group" "clickhouse" {
  name_prefix = "${local.name_prefix}-clickhouse-"
  description = "Security group for ClickHouse instances"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${local.name_prefix}-clickhouse-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# HTTP interface (8123) from EKS nodes
resource "aws_security_group_rule" "clickhouse_http" {
  count = length(var.allowed_security_group_ids)

  type                     = "ingress"
  from_port                = 8123
  to_port                  = 8123
  protocol                 = "tcp"
  source_security_group_id = var.allowed_security_group_ids[count.index]
  security_group_id        = aws_security_group.clickhouse.id
  description              = "ClickHouse HTTP interface from authorized SG"
}

# Native protocol (9000) from EKS nodes
resource "aws_security_group_rule" "clickhouse_native" {
  count = length(var.allowed_security_group_ids)

  type                     = "ingress"
  from_port                = 9000
  to_port                  = 9000
  protocol                 = "tcp"
  source_security_group_id = var.allowed_security_group_ids[count.index]
  security_group_id        = aws_security_group.clickhouse.id
  description              = "ClickHouse native protocol from authorized SG"
}

# Inter-node communication (for cluster replication)
resource "aws_security_group_rule" "clickhouse_internode" {
  type                     = "ingress"
  from_port                = 9009
  to_port                  = 9009
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.clickhouse.id
  security_group_id        = aws_security_group.clickhouse.id
  description              = "ClickHouse inter-node replication"
}

resource "aws_security_group_rule" "clickhouse_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.clickhouse.id
  description       = "Allow all outbound traffic"
}

###############################################################################
# Launch Template
###############################################################################

resource "aws_launch_template" "clickhouse" {
  name_prefix   = "${local.name_prefix}-clickhouse-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.clickhouse.arn
  }

  vpc_security_group_ids = [aws_security_group.clickhouse.id]

  block_device_mappings {
    device_name = "/dev/sda1"

    ebs {
      volume_size           = 30
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  # Data volume for ClickHouse
  block_device_mappings {
    device_name = "/dev/sdf"

    ebs {
      volume_size           = var.volume_size
      volume_type           = local.volume_type
      iops                  = local.volume_iops
      throughput            = local.volume_throughput
      encrypted             = true
      delete_on_termination = false
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tpl", {
    environment   = var.environment
    region        = data.aws_region.current.name
    backup_bucket = aws_s3_bucket.clickhouse_backups.id
  }))

  tag_specifications {
    resource_type = "instance"

    tags = {
      Name = "${local.name_prefix}-clickhouse"
    }
  }

  tag_specifications {
    resource_type = "volume"

    tags = {
      Name = "${local.name_prefix}-clickhouse-vol"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

###############################################################################
# Auto Scaling Group
###############################################################################

resource "aws_autoscaling_group" "clickhouse" {
  name_prefix         = "${local.name_prefix}-clickhouse-"
  vpc_zone_identifier = var.private_subnet_ids
  min_size            = var.node_count
  max_size            = var.node_count
  desired_capacity    = var.node_count

  launch_template {
    id      = aws_launch_template.clickhouse.id
    version = "$Latest"
  }

  health_check_type         = "EC2"
  health_check_grace_period = 300

  target_group_arns = [
    aws_lb_target_group.clickhouse_http.arn,
    aws_lb_target_group.clickhouse_native.arn,
  ]

  tag {
    key                 = "Name"
    value               = "${local.name_prefix}-clickhouse"
    propagate_at_launch = true
  }

  tag {
    key                 = "Service"
    value               = "clickhouse"
    propagate_at_launch = true
  }

  lifecycle {
    create_before_destroy = true
  }
}

###############################################################################
# Network Load Balancer
###############################################################################

resource "aws_lb" "clickhouse" {
  name               = "${local.name_prefix}-ch-nlb"
  internal           = true
  load_balancer_type = "network"
  subnets            = var.private_subnet_ids

  enable_cross_zone_load_balancing = true

  tags = {
    Name = "${local.name_prefix}-clickhouse-nlb"
  }
}

# HTTP target group (port 8123)
resource "aws_lb_target_group" "clickhouse_http" {
  name     = "${local.name_prefix}-ch-http"
  port     = 8123
  protocol = "TCP"
  vpc_id   = var.vpc_id

  health_check {
    protocol            = "HTTP"
    port                = 8123
    path                = "/ping"
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
  }

  tags = {
    Name = "${local.name_prefix}-clickhouse-http-tg"
  }
}

# Native protocol target group (port 9000)
resource "aws_lb_target_group" "clickhouse_native" {
  name     = "${local.name_prefix}-ch-native"
  port     = 9000
  protocol = "TCP"
  vpc_id   = var.vpc_id

  health_check {
    protocol            = "TCP"
    port                = 9000
    healthy_threshold   = 3
    unhealthy_threshold = 3
    interval            = 10
  }

  tags = {
    Name = "${local.name_prefix}-clickhouse-native-tg"
  }
}

# Listeners
resource "aws_lb_listener" "clickhouse_http" {
  load_balancer_arn = aws_lb.clickhouse.arn
  port              = 8123
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.clickhouse_http.arn
  }
}

resource "aws_lb_listener" "clickhouse_native" {
  load_balancer_arn = aws_lb.clickhouse.arn
  port              = 9000
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.clickhouse_native.arn
  }
}
