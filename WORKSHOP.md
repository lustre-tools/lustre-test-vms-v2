# Workshop Lab — Quick Notes

GCP project: `ltvm-workshop-playground` (us-central1-a)

## VMs

| Name  | Role            | Type             | vCPU | RAM    | Disk  | External IP            |
|-------|-----------------|------------------|------|--------|-------|------------------------|
| hop   | bastion         | e2-small         | 2    | 2 GiB  | 30 GB | 35.238.97.125 (static) |
| host1 | workshop host A | n2-standard-32   | 32   | 128 GiB| 256 GB| none                   |
| host2 | workshop host B | n2-standard-32   | 32   | 128 GiB| 256 GB| none                   |

Hosts have nested virt enabled. Cloud NAT (`workshop-nat`) provides egress for internal-only hosts.

DNS: `devday.mulberrytree.us` → `35.238.97.125` (manually maintained).

Capacity: 28 microVMs/host × 2 = 56. Realistic peak: 10 attendees × 4 VMs = 40.

## Auth

Password-only SSH everywhere. No keys to distribute.

- **Hop**: `ssh admin@devday.mulberrytree.us` — password `blew-hurry-throughout-rate`
- **Hosts** (from hop): `ssh admin@host1` / `ssh admin@host2` — password `admin`
- **MicroVMs** (from host): `ssh root@<vm-name>` — managed by `ltvm`

`admin` has passwordless sudo on all three VMs.

Hop ↔ host1 ↔ host2 are passwordless via shared key (workshop key in `~admin/.ssh/`).

## Cost

- Workshop day (8h, both hosts running): **~$25 compute + ~$0.07 disk = ~$25**
- Idle (hosts stopped, hop running, disks attached): **~$51/month** (dominated by disks)
- Hop e2-small: ~$12/month, 24/7

## Operations

```bash
# Stop hosts when not in use
gcloud compute instances stop host1 host2 --zone=us-central1-a

# Bring them back
gcloud compute instances start host1 host2 --zone=us-central1-a

# Resize a host (must be stopped)
gcloud compute instances set-machine-type host1 \
    --zone=us-central1-a --machine-type=n2-standard-32
```

## ltvm setup on hosts

```bash
sudo dnf install -y git
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/lustre-tools/lustre-test-vms.git ~/lustre-test-vms
cd ~/lustre-test-vms && ~/.local/bin/uv sync
sudo ./ltvm install
sudo ltvm fetch rocky9      # pulls kernel + image + build container
```
