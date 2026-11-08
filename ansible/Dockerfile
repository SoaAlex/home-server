# Control node for the NAS rebuild: Ansible + Terraform + age, nothing
# installed on the host. Built and started by `ansible/run`.
# Every download is pinned by version and SHA-256 (pip: --require-hashes).
FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72

ARG TERRAFORM_VERSION=1.17.1
ARG TERRAFORM_SHA256=4638628058e71a55212b1c563712eac03b902128f40740f3dc8174df12135061
ARG AGE_VERSION=1.3.2
ARG AGE_SHA256=cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10
# Same file through the GitHub API, used when github.com/.../releases/download answers 5xx.
ARG AGE_API_ASSET=535541903

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client git curl unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN set -eu; cd /tmp \
 && curl -fsSL --retry 5 --retry-all-errors -o tf.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
 && echo "${TERRAFORM_SHA256}  tf.zip" | sha256sum -c - \
 && unzip -q tf.zip terraform -d /usr/local/bin && rm tf.zip \
 && { curl -fsSL --retry 3 --retry-all-errors -o age.tgz "https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-amd64.tar.gz" \
      || curl -fsSL --retry 3 --retry-all-errors -H 'Accept: application/octet-stream' -o age.tgz "https://api.github.com/repos/FiloSottile/age/releases/assets/${AGE_API_ASSET}"; } \
 && echo "${AGE_SHA256}  age.tgz" | sha256sum -c - \
 && tar -xzf age.tgz && install -m 755 age/age age/age-keygen /usr/local/bin/ && rm -rf age age.tgz

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt

# ansible-galaxy checks each collection tarball against the digest Galaxy publishes.
COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml -p /usr/share/ansible/collections \
 && rm /tmp/requirements.yml

# Same uid/gid as the NAS user, so files written in the mounted repo keep
# their 1000:10 owner. ssh refuses to run for a uid without a passwd entry.
RUN useradd -u 1000 -g 10 -m -s /bin/bash ansible \
 && git config --system --add safe.directory '*'
USER ansible
ENV ANSIBLE_FORCE_COLOR=1
