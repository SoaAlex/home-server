## Parameters
PI_URL=192.168.1.53
RAID1_HDD1=/dev/sda1
RAID1_HDD2=/dev/sdb1
DUCKDNS_TOKEN=""
DUCKDNS_DOMAINS=""


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

# Enable IPv6
sudo echo "{\
	"ipv6": true,\
	"fixed-cidr-v6": "fd7a:fb81:cc8c::/48",\
	"ip6tables": true,\
	"experimental": true\
    }" > /etc/docker/daemon.json

# Install Portainer
sudo docker pull portainer/portainer-ce:latest
sudo docker run -d -p 9000:9000 --name=portainer --restart=always -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data portainer/portainer-ce:latest
read -p "Docker and docker compose installed. Please setup portainer at http://${PI_URL}:9000" -n 1 -r


## 2. Setup Raid 1
# 2.1 Check if Raid is already active
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
else
    read -p "Please setup Raid 1 manually following this guide: http://emery.claude.free.fr/nas-raid1-raspberry.html" -n 1 -r
    sudo mdadm --create --verbose /dev/md0 --level=1 --raid-devices=2 ${RAID1_HDD1} ${RAID1_HDD2}
fi

# 2.2. Auto mount
sudo echo "/dev/md0 /mnt/raid ext4 defaults,noatime,nofail 0 0" > /etc/fstab 

# 2.3. Auto shutdown disks
sudo hdparm -S 60 ${RAID1_HDD1}
sudo hdparm -S 60 ${RAID1_HDD2}


## 3. Setup DuckDNS 
sudo mkdir /home/pi/home-server/duckdns
sudo touch /home/pi/home-server/duckdns/duck.sh
sudo echo 'echo url="https://www.duckdns.org/update?domains=${DUCKDNS_DOMAINS}&token=${DUCKDNS_TOKEN}" | curl -k -o ~/duckdns/duck.log -K -' > duck.sh
sudo chmod 700 duck.sh
read -p "Please paste this into crontab -e: */5 * * * * home/pi/home-server/duckdns/duck.sh >/dev/null 2>&1" -n 1 -r


## 4. Install home server services using docker-compose:
cd /home/pi/home-server
docker-compose up -d


## Random commands helper
# sudo docker exec --user www-data -it nextcloud-aio-nextcloud php occ files:scan --all

# convmv -f utf-8 -t utf-8 -r --notest --nfc

# proxy_set_header Host $host;
# proxy_set_header X-Forwarded-Proto $scheme;
# proxy_set_header X-Real-IP $remote_addr;
# proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
# proxy_max_temp_file_size 0;
# client_max_body_size 0;