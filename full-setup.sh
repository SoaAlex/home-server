## Parameters
PI_URL=192.168.1.53


## 0. Update
sudo apt update
sudo apt upgrade


## 1. Install Docker / Docker Compose and Portainer
# Docker
curl -sSL https://get.docker.com | sh
sudo usermod -aG docker pi

# Docker compose
sudo apt-get install libffi-dev libssl-dev
sudo apt install python3-dev
sudo apt-get install -y python3 python3-pip
sudo pip3 install docker-compose
sudo systemctl enable docker

# Portainer
sudo docker pull portainer/portainer-ce:latest
sudo docker run -d -p 9000:9000 --name=portainer --restart=always -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data portainer/portainer-ce:latest
read -p "Docker and docker compose installed. Please setup portainer at http://${PI_URL}:9000" -n 1 -r


# 2. Setup Raid 1
# Check if Raid is already active
if cat /proc/mdstat | grep -q 'active'; then
    echo "Raid 1 is already active"
elif cat /proc/mdstat | grep -q 'sync'; then
    echo "Raid 1 is syncing... Please wait for completion"
    while cat /proc/mdstat | grep -q 'sync'
    do
        sleep 60
    done
    if cat /proc/mdstat | grep -q 'active'; then
        echo "Raid 1 sync complete"
    else
        echo "Raid 1 sync failed. Please continue manually"
        exit 0
    fi
fi

# sudo docker exec --user www-data -it nextcloud-aio-nextcloud php occ files:scan --all
# convmv -f utf-8 -t utf-8 -r --notest --nfc