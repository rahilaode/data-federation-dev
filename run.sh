
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