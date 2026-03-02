#!/bin/bash
set -euo pipefail

###############################################################################
# ClickHouse installation and configuration
# Environment: ${environment}
###############################################################################

export DEBIAN_FRONTEND=noninteractive

# Wait for cloud-init to finish
cloud-init status --wait

# ----- Mount the data volume -----
DATA_DEVICE="/dev/nvme1n1"

# Wait for the device to appear (EBS can take a moment)
for i in $(seq 1 30); do
  if [ -b "$DATA_DEVICE" ]; then
    break
  fi
  echo "Waiting for $DATA_DEVICE to appear... ($i/30)"
  sleep 2
done

if [ ! -b "$DATA_DEVICE" ]; then
  echo "ERROR: Data device $DATA_DEVICE not found"
  exit 1
fi

# Format only if not already formatted
if ! blkid "$DATA_DEVICE" | grep -q ext4; then
  mkfs.ext4 -L clickhouse-data "$DATA_DEVICE"
fi

mkdir -p /var/lib/clickhouse
echo "LABEL=clickhouse-data /var/lib/clickhouse ext4 defaults,noatime,nodiratime 0 2" >> /etc/fstab
mount -a

# ----- Install ClickHouse -----
apt-get update -y
apt-get install -y apt-transport-https ca-certificates curl gnupg

# Add ClickHouse repository
curl -fsSL 'https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key' | \
  gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg] https://packages.clickhouse.com/deb stable main" | \
  tee /etc/apt/sources.list.d/clickhouse.list

apt-get update -y
apt-get install -y clickhouse-server clickhouse-client

# ----- Configure ClickHouse -----
chown -R clickhouse:clickhouse /var/lib/clickhouse

cat > /etc/clickhouse-server/config.d/custom.xml <<'XMLEOF'
<clickhouse>
    <logger>
        <level>information</level>
        <log>/var/log/clickhouse-server/clickhouse-server.log</log>
        <errorlog>/var/log/clickhouse-server/clickhouse-server.err.log</errorlog>
        <size>100M</size>
        <count>5</count>
    </logger>

    <listen_host>0.0.0.0</listen_host>
    <http_port>8123</http_port>
    <tcp_port>9000</tcp_port>
    <interserver_http_port>9009</interserver_http_port>

    <max_connections>4096</max_connections>
    <keep_alive_timeout>3</keep_alive_timeout>
    <max_concurrent_queries>200</max_concurrent_queries>

    <path>/var/lib/clickhouse/</path>
    <tmp_path>/var/lib/clickhouse/tmp/</tmp_path>

    <mark_cache_size>5368709120</mark_cache_size>

    <merge_tree>
        <max_suspicious_broken_parts>5</max_suspicious_broken_parts>
    </merge_tree>
</clickhouse>
XMLEOF

cat > /etc/clickhouse-server/config.d/backup.xml <<XMLEOF
<clickhouse>
    <backups>
        <allowed_disk>backups</allowed_disk>
        <allowed_path>/var/lib/clickhouse/backups/</allowed_path>
    </backups>
    <storage_configuration>
        <disks>
            <backups>
                <type>s3</type>
                <endpoint>https://${backup_bucket}.s3.${region}.amazonaws.com/backups/</endpoint>
                <use_environment_credentials>true</use_environment_credentials>
            </backups>
        </disks>
    </storage_configuration>
</clickhouse>
XMLEOF

cat > /etc/clickhouse-server/users.d/custom.xml <<'XMLEOF'
<clickhouse>
    <profiles>
        <default>
            <max_memory_usage>10000000000</max_memory_usage>
            <max_execution_time>300</max_execution_time>
            <load_balancing>random</load_balancing>
        </default>
    </profiles>
</clickhouse>
XMLEOF

# ----- Set up log rotation -----
cat > /etc/logrotate.d/clickhouse <<'LOGEOF'
/var/log/clickhouse-server/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LOGEOF

# ----- Set up daily backup cron -----
cat > /etc/cron.d/clickhouse-backup <<'CRONEOF'
0 2 * * * clickhouse clickhouse-client --query "BACKUP DATABASE default TO S3('https://${backup_bucket}.s3.${region}.amazonaws.com/backups/daily/$(date +\%Y-\%m-\%d)/')" >> /var/log/clickhouse-server/backup.log 2>&1
CRONEOF

# ----- Start ClickHouse -----
systemctl enable clickhouse-server
systemctl start clickhouse-server

# ----- Install CloudWatch agent for log shipping -----
curl -sL "https://s3.${region}.amazonaws.com/amazoncloudwatch-agent-${region}/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb" -o /tmp/amazon-cloudwatch-agent.deb
dpkg -i /tmp/amazon-cloudwatch-agent.deb

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'CWEOF'
{
  "agent": {
    "metrics_collection_interval": 60
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/clickhouse-server/clickhouse-server.log",
            "log_group_name": "/apdl/${environment}/clickhouse",
            "log_stream_name": "{instance_id}/server",
            "retention_in_days": 30
          },
          {
            "file_path": "/var/log/clickhouse-server/clickhouse-server.err.log",
            "log_group_name": "/apdl/${environment}/clickhouse",
            "log_stream_name": "{instance_id}/error",
            "retention_in_days": 30
          }
        ]
      }
    }
  },
  "metrics": {
    "namespace": "APDL/ClickHouse",
    "metrics_collected": {
      "disk": {
        "measurement": ["used_percent"],
        "resources": ["*"]
      },
      "mem": {
        "measurement": ["mem_used_percent"]
      },
      "cpu": {
        "measurement": ["cpu_usage_active"]
      }
    }
  }
}
CWEOF

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

echo "ClickHouse setup complete for environment: ${environment}"
