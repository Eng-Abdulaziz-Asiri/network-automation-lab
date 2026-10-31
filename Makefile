.RECIPEPREFIX = >
LAB = topology.clab.yml
.PHONY: all deploy configure wait test backup drift destroy

all: deploy configure wait test   ## Build + configure + verify everything

deploy:       ## Start routers and links
> containerlab deploy -t $(LAB) --reconfigure

configure:    ## Push configs with Ansible
> cd ansible && ansible-playbook -i inventory.yml deploy.yml

wait:         ## Let OSPF/BGP converge
> @echo "Waiting 30s for OSPF/BGP to converge..." && sleep 30

test:         ## Run health summary
> python3 scripts/summary.py

destroy:      ## Tear down the lab
> containerlab destroy -t $(LAB) --cleanup

backup:       ## Save running configs to backups/
> cd ansible && ansible-playbook -i inventory.yml backup.yml

drift:        ## Compare running configs with the Source of Truth
> python3 scripts/drift.py
