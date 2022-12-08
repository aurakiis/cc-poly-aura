# cc_poly_aura

Preparation:
- Copy the labuser.pem file into the project root
- Update the credentials into the local credentials file found on the .aws folder
- Install boto3 and paramiko on your computer (pip install)

Command to run the script:
- python3 script.py

To connect to the instances with Linux / Mac:
- ssh -i labsuser.pem ubuntu@IP_ADDRESS

Commands to be executed on the master node:
- /bin/bash mysql_execution.sh
- /bin/bash mysql_execution2.sh
- /bin/bash sysbench.sh

Command to see the results of sysbench on the cluster (on master node):
- cat results.txt

Command to see the results of sysbench on the stand-alone:
- cat results.txt
