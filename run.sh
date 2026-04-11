
# docker network create ascam-networks

# data source
docker-compose -f ./setup/data-source/docker-compose.yaml down -v
docker-compose -f ./setup/data-source/docker-compose.yaml up -d

# data federation
docker-compose -f ./setup/data-federation/docker-compose.yaml down -v
docker-compose -f ./setup/data-federation/docker-compose.yaml up --build --detach

# vkg-system
docker-compose -f ./setup/vkg-system/docker-compose.yaml down -v
docker-compose -f ./setup/vkg-system/docker-compose.yaml up --build --detach

# ascam schema monitor
docker-compose -f ./setup/ascam/schema-monitor/docker-compose.yaml down -v
docker-compose -f ./setup/ascam/schema-monitor/docker-compose.yaml up --build --detach

sleep 40

# register postgres connector with ascam schema monitor
curl -X POST -H "Content-Type: application/json" \
     --data @./setup/ascam/schema-monitor/register-postgres.json \
     http://localhost:8083/connectors

# register mysql connector with ascam schema monitor
curl -X POST -H "Content-Type: application/json" \
     --data @./setup/ascam/schema-monitor/register-mysql.json \
     http://localhost:8083/connectors

