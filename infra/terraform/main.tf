###############################################################################
# Data Sources
###############################################################################

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_caller_identity" "current" {}

###############################################################################
# Networking
###############################################################################

module "networking" {
  source = "./modules/networking"

  environment = var.environment
  project     = var.project_name
  vpc_cidr    = var.vpc_cidr
  azs         = slice(data.aws_availability_zones.available.names, 0, 3)
}

###############################################################################
# EKS
###############################################################################

module "eks" {
  source = "./modules/eks"

  environment         = var.environment
  project             = var.project_name
  cluster_version     = var.eks_cluster_version
  vpc_id              = module.networking.vpc_id
  private_subnet_ids  = module.networking.private_subnet_ids
  node_instance_types = var.eks_node_instance_types
  min_nodes           = var.eks_min_nodes
  max_nodes           = var.eks_max_nodes
  desired_nodes       = var.eks_desired_nodes
}

###############################################################################
# Redis
###############################################################################

module "redis" {
  source = "./modules/redis"

  environment                = var.environment
  project                    = var.project_name
  vpc_id                     = module.networking.vpc_id
  private_subnet_ids         = module.networking.private_subnet_ids
  node_type                  = var.redis_node_type
  num_cache_nodes            = var.redis_num_cache_nodes
  allowed_security_group_ids = [module.eks.node_security_group_id]
}

###############################################################################
# ClickHouse
###############################################################################

module "clickhouse" {
  source = "./modules/clickhouse"

  environment                = var.environment
  project                    = var.project_name
  vpc_id                     = module.networking.vpc_id
  private_subnet_ids         = module.networking.private_subnet_ids
  instance_type              = var.clickhouse_instance_type
  volume_size                = var.clickhouse_volume_size
  node_count                 = var.clickhouse_node_count
  allowed_security_group_ids = [module.eks.node_security_group_id]
}

###############################################################################
# PostgreSQL
###############################################################################

module "postgres" {
  source = "./modules/postgres"

  environment                = var.environment
  project                    = var.project_name
  vpc_id                     = module.networking.vpc_id
  private_subnet_ids         = module.networking.private_subnet_ids
  instance_class             = var.postgres_instance_class
  allocated_storage          = var.postgres_allocated_storage
  engine_version             = var.postgres_engine_version
  database_name              = var.postgres_database_name
  allowed_security_group_ids = [module.eks.node_security_group_id]
}

###############################################################################
# Monitoring
###############################################################################

module "monitoring" {
  source = "./modules/monitoring"

  environment        = var.environment
  project            = var.project_name
  aws_region         = var.aws_region
  eks_cluster_name   = module.eks.cluster_name
  eks_oidc_issuer    = module.eks.oidc_issuer
  alert_email        = var.alert_email
  redis_cluster_id   = module.redis.replication_group_id
  rds_instance_id    = module.postgres.db_instance_id
  clickhouse_asg_name = module.clickhouse.asg_name
}
