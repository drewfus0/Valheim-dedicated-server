#!/bin/sh
DOCKER_IMAGE=valheim_server
DOCKER_DATA_VOLUME=valheim_server_data

set -e

if ! docker ps > /dev/null 2>&1
then
    echo "Is docker installed and is your user a member of the 'docker' group?"
    exit 1
fi

if [ $# != 1 ]
then
    echo "Usage: $0 SERVER_SCRIPT"
    exit 2
fi

if ! docker inspect "${DOCKER_IMAGE}" > /dev/null 2>&1
then
    docker build docker -t "${DOCKER_IMAGE}"
fi

docker run --rm -it -v "${DOCKER_DATA_VOLUME}:/root/.config/unity3d/IronGate/Valheim" -v "$PWD:/irongate" "${DOCKER_IMAGE}" "$1"
