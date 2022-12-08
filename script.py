# Keys are defined in configuration file
# MAKE SURE YOU UPDATED YOUR .AWS/credentials file
# MAKE SURE boto3 and paramiko are installed using pip
import boto3
import time
from datetime import date
from datetime import datetime, timedelta

import paramiko

# allows us to geth the path for the pem file
from pathlib import Path

def get_project_root() -> Path:
    """
    Function for getting the path where the program is executed
    @ return: returns the parent path of the path were the program is executed
    """
    return Path(__file__).parent

"""
The user data constants are used to setup and download programs on the instances
They are passed as arguments in the create instance step
There are different user data variables for stand-alone and cluster, including data nodes and management node
"""

userdata_standalone="""#!/bin/bash
cd /home/ubuntu
sudo apt update
sudo apt install wget
sudo apt install unzip
yes | sudo apt install mysql-server
yes | sudo apt-get install sysbench

# running mysql_secure_installation commands
sudo mysql -e "UPDATE mysql.user SET Password = PASSWORD('mypassword) WHERE User = 'root'"
sudo mysql -e "DROP USER ''@'localhost'"
sudo mysql -e "DROP USER ''@'$(hostname)'"
sudo mysql -e "DROP DATABASE test"
sudo mysql -e "FLUSH PRIVILEGES"

# downloading sakila
mkdir tmp
cd tmp
sudo wget http://downloads.mysql.com/docs/sakila-db.zip
unzip sakila-db.zip
cd ..

# setting up sakila db
sudo mysql -u root -p"mypassword" <<EOF
SOURCE tmp/sakila-db/sakila-schema.sql;
SOURCE tmp/sakila-db/sakila-data.sql;
USE sakila;
exit
EOF

sudo sysbench oltp_read_write --table-size=1000000 --mysql-db=sakila --mysql-user=root --mysql-password=mypassword prepare
sudo sysbench oltp_read_write --table-size=1000000 --mysql-db=sakila --mysql-user=root --mysql-password=mypassword run > results.txt

"""

userdata_nodes="""#!/bin/bash

sudo apt update
sudo apt install wget
sudo service mysqld stop
yes | sudo apt install yum
sudo yum remove mysql-server mysql mysql-devel

mkdir -p /opt/mysqlcluster/home
cd /opt/mysqlcluster/home
wget http://dev.mysql.com/get/Downloads/MySQL-Cluster-7.2/mysql-cluster-gpl-7.2.1-linux2.6-x86_64.tar.gz
sudo tar -xf mysql-cluster-gpl-7.2.1-linux2.6-x86_64.tar.gz
sudo ln -s mysql-cluster-gpl-7.2.1-linux2.6-x86_64 mysqlc

echo 'export MYSQLC_HOME=/opt/mysqlcluster/home/mysqlc' > /etc/profile.d/mysqlc.sh
echo 'export PATH=$MYSQLC_HOME/bin:$PATH' >> /etc/profile.d/mysqlc.sh

sudo mkdir -p /opt/mysqlcluster/deploy/ndb_data

"""

userdata_masternode="""#!/bin/bash

sudo apt update
sudo apt install wget
sudo service mysqld stop
yes | sudo apt install yum
sudo yum remove mysql-server mysql mysql-devel

mkdir -p /opt/mysqlcluster/home
cd /opt/mysqlcluster/home
wget http://dev.mysql.com/get/Downloads/MySQL-Cluster-7.2/mysql-cluster-gpl-7.2.1-linux2.6-x86_64.tar.gz
sudo tar -xf mysql-cluster-gpl-7.2.1-linux2.6-x86_64.tar.gz
sudo ln -s mysql-cluster-gpl-7.2.1-linux2.6-x86_64 mysqlc
sudo chmod -R 777 mysqlc

echo 'export MYSQLC_HOME=/opt/mysqlcluster/home/mysqlc' > /etc/profile.d/mysqlc.sh
echo 'export PATH=$MYSQLC_HOME/bin:$PATH' >> /etc/profile.d/mysqlc.sh

sudo mkdir -p /opt/mysqlcluster/deploy
cd /opt/mysqlcluster/deploy
sudo mkdir conf
sudo mkdir mysqld_data
sudo mkdir ndb_data

sudo chmod 777 /opt/mysqlcluster/deploy/conf
cd conf
echo -n > my.cnf
sudo chmod 664 my.cnf
sudo cat <<EOF >my.cnf
[mysqld]
ndbcluster
datadir=/opt/mysqlcluster/deploy/mysqld_data
basedir=/opt/mysqlcluster/home/mysqlc
port=3306
EOF

cd /opt/mysqlcluster/deploy
sudo chmod -R 777 mysqld_data
sudo chmod -R 777 ndb_data

"""


def createSecurityGroup(ec2_client):
    """
        The function creates a new security group in AWS
        The function retrievs the vsp_id from the AWS portal, as it is personal and needed for creating a new group
        It then creates the security group using boto3 package
        then it waits for the creation
        then it assigns new rules to the security group

        Parameters
        ----------
        ec2_client
            client that allows for sertain functions using boto3

        Returns
        -------
        SECURITY_GROUP : list[str]
            list of the created security group ids
        vpc_id : str
            the vpc_id as it is needed for other operations

        Errors
        -------
        The function throws an error if a security group with the same name already exists in your AWS

    """
    # Create security group, using SSH, HTTP, 1186 & MySQL access available from anywhere
    groups = ec2_client.describe_security_groups()
    vpc_id = groups["SecurityGroups"][0]["VpcId"]

    new_group = ec2_client.create_security_group(
        Description="SSH and HTTP access",
        GroupName="Cloud Computing Project",
        VpcId=vpc_id
    )

    # Wait for the security group to exist!
    new_group_waiter = ec2_client.get_waiter('security_group_exists')
    new_group_waiter.wait(GroupNames=["Cloud Computing Project"])

    group_id = new_group["GroupId"]

    rule_creation = ec2_client.authorize_security_group_ingress(
        GroupName="Cloud Computing Project",
        GroupId=group_id,
        IpPermissions=[
        {
            'FromPort': 0,
            'ToPort': 65535,
            'IpProtocol': '-1',
            'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
        }]
    )

    SECURITY_GROUP = [group_id]
    return SECURITY_GROUP, vpc_id

def getAvailabilityZones(ec2_client):
    """
        Retrieving the subnet ids for availability zones
        they are required to assign for example instances to a specific availabilityzone

        Parameters
        ----------
        ec2_client
            client of boto3 tho access certain methods related to AWS EC2

        Returns
        -------
        dict
            a dictonary, with availability zone name as key and subnet id as value

        """
    # Availability zones
    response = ec2_client.describe_subnets()

    availabilityzones = {}
    for subnet in response.get('Subnets'):
        # print(subnet)
        availabilityzones.update({subnet.get('AvailabilityZone'): subnet.get('SubnetId')})

    return availabilityzones

def createInstance(ec2, INSTANCE_TYPE, COUNT, SECURITY_GROUP, SUBNET_ID, userdata):
    """
        function that creates EC2 instances on AWS

        Parameters
        ----------
        ec2 : client
            ec2 client to perform actions on AWS EC2 using boto3
        INSTANCE_TYPE : str
            name of the desired instance type.size
        COUNT : int
            number of instances to be created
        SECURITY_GROUP : array[str]
            array of the security groups that should be assigned to the instance
        SUBNET_ID : str
            subnet id that assigns the instance to a certain availability zone
        userdata : str
            string that setups and downloads programs on the instance at creation

        Returns
        -------
        array
            list of all created instances, including their data

        """
    # Don't change these
    KEY_NAME = "vockey"
    INSTANCE_IMAGE = "ami-08d4ac5b634553e16"

    return ec2.create_instances(
        ImageId=INSTANCE_IMAGE,
        MinCount=COUNT,
        MaxCount=COUNT,
        InstanceType=INSTANCE_TYPE,
        KeyName=KEY_NAME,
        SecurityGroupIds=SECURITY_GROUP,
        SubnetId=SUBNET_ID,
        UserData=userdata
    )

def createInstances(ec2_client, ec2, SECURITY_GROUP, availabilityZones, userdata):
    """
        function that retrievs and processes attributes as well as defining the amount and types of instances to be created
        getting the decired subnet id
        calling function create instance to create the instances
        parces the return to just return the ids and ips of the instances
        currently handle only creation of one instance

        Parameters
        ----------
        ec2_client : client
            Boto3 client to access certain function to controll AWS CLI
        ec2 : client
            Boto3 client to access certain function to controll AWS CLI
        SECURITY_GROUP : array[str]
            list of security groups to assign to instances
        availabilityZones : dict{str, str}
            dict of availability zone names an key and subnet ids as value
        userdata : str
            script to setup instances

        Returns
        -------
        array
            containg instance id, puublic ip and private ip
        """
    # Get wanted availability zone
    availability_zone_1a = availabilityZones.get('us-east-1a')

    # Use t2.micro for deployment/demo
    instances_t2_a = createInstance(ec2, "t2.micro", 1, SECURITY_GROUP, availability_zone_1a, userdata)

    instance_ids = []

    instance_ids.append(instances_t2_a[0].id)

    instances_t2_a[0].wait_until_running()
    instances_t2_a[0].reload()

    ip = instances_t2_a[0].public_ip_address
    privateip = instances_t2_a[0].private_ip_address
    print(ip)
    print(privateip)

    # Wait for all instances to be active!
    instance_running_waiter = ec2_client.get_waiter('instance_running')
    instance_running_waiter.wait(InstanceIds=(instance_ids))

    return [instance_ids, ip, privateip]

def getParamikoClient():
    """
        Retrievs the users PEM file and creates a paramiko client required to ssh into the instances
        Returns
        -------
        client
            the paramiko client
        str
            the access key from the PEM file
        """
    path = str(get_project_root()).replace('\\', '/')
    print("path", path)
    accesKey = paramiko.RSAKey.from_private_key_file(path + "/labsuser.pem")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    return client, accesKey

def send_command(client, command):
    """
        function that sends command to an instance using paramiko
        print possible errors and return values
        Parameters
        ----------
        client : client
            the paramiko client required to connect to the intance usin ssh
        command : str
            The desired commands are sent to the instance
        Returns
        -------
        str
            returns the return value of commands
        """
    try:
        stdin, stdout, stderr = client.exec_command(command)
        # the read() function reads the output in bit form
        print("stderr.read():", stderr.read())
        # converts the bit string to str
        output = stdout.read().decode('ascii').split("\n")
        print("output", output)
        return output
    except:
        print("error occured in sending command")

def createMasterFiles(ip, client, accesKey, privateipMaster, privateip1, privateip2, privateip3):
    """

        Creating setup files on master so that the files can be executed. These files are:
        1) installation of libncurses5 and creation of config.ini file
        2) MySQL setup file
        3) MySQL server setup after node connections
        4) MySQL secure installation and sakila
        5) Sysbench preparation and run
        Also changing access rights of the newly created files to include rwx.
        ----------
        ip : str
            ip adress of the instance we wish to connect to
        client : client
            paramiko client to ssh into instance
        accesKey : str
            private accesskey to gain access to instance
        privateipMaster : str
            private ip adress of the master instance needed for the config.ini
        privateip1 : str
            private ip adress of the first instance needed for the config.ini
        privateip2 : str
            private ip adress of the second instance needed for the config.ini
        privateip3 : str
            private ip adress of the third instance needed for the config.ini
    """

    try:
        client.connect(hostname=ip, username="ubuntu", pkey=accesKey)
    except:
        print("could not connect to client")

    # writing the config.ini file and installing libncurses5
    file_path = "write_config.sh"
    file_path2 = "mysql_setup.sh"
    file_path3 = "mysql_execution.sh"
    file_path4 = "mysql_execution2.sh"
    file_path5 = "sysbench.sh"

    file_content = """#!/bin/bash
        source /etc/profile.d/mysqlc.sh
        sudo apt -y install libncurses5
        cd /opt/mysqlcluster/deploy/conf/

        sudo cat <<EOF >config.ini
        [ndb_mgmd]
        hostname=""" + privateipMaster + """
        datadir=/opt/mysqlcluster/deploy/ndb_data
        nodeid=1

        [ndbd default]
        noofreplicas=1
        datadir=/opt/mysqlcluster/deploy/ndb_data

        [ndbd]
        hostname=""" + privateip1 + """
        nodeid=2

        [ndbd]
        hostname=""" + privateip2 + """
        nodeid=3

        [ndbd]
        hostname=""" + privateip3 + """
        nodeid=4

        [mysqld]
        nodeid=50
        """

    # writing the mysql setup file
    file_content2 = """#!/bin/bash
        cd /opt/mysqlcluster/deploy
        sudo chmod -R 777 mysqld_data
        sudo chmod -R 777 ndb_data
        cd /opt/mysqlcluster/home
        sudo chmod -R 777 mysqlc

        cd /opt/mysqlcluster/home/mysqlc
        sudo scripts/mysql_install_db --no-defaults --datadir=/opt/mysqlcluster/deploy/mysqld_data
        sudo /opt/mysqlcluster/home/mysqlc/bin/ndb_mgmd -f /opt/mysqlcluster/deploy/conf/config.ini --initial --configdir=/opt/mysqlcluster/deploy/conf/
        ndb_mgm -e show
        ndb_mgm -e 'all status'
    """

    # writing the setup file for the mysql server start
    file_content3 = """#!/bin/bash
        # Check statuses
        ndb_mgm -e show
        ndb_mgm -e 'all status'

        # After all nodes have started
        cd /opt/mysqlcluster/deploy
        sudo chmod -R 777 mysqld_data
        sudo chmod -R 777 ndb_data
        cd /opt/mysqlcluster/home
        sudo chmod -R 777 mysqlc

        mysqld --defaults-file=/opt/mysqlcluster/deploy/conf/my.cnf --user=root &
    """
    # writing the setup file for the sakila and mysql_secure_installation
    file_content4 = """
        # running mysql_secure_installation commands
        mysql -uroot -e "UPDATE mysql.user SET Password = PASSWORD('mypassword') WHERE User = 'root'"
        mysql -uroot -e "DROP USER ''@'localhost'"
        mysql -uroot -e "DROP USER ''@'$(hostname)'"
        mysql -uroot -e "DROP DATABASE test"
        mysql -uroot -e "FLUSH PRIVILEGES"

        # downloading sakila
        sudo apt install unzip
        cd ~
        mkdir tmp
        cd tmp
        sudo wget http://downloads.mysql.com/docs/sakila-db.zip
        unzip sakila-db.zip
        cd ..

        # setting up sakila db
        mysql -u root -p"mypassword" <<EOF
        SOURCE tmp/sakila-db/sakila-schema.sql;
        SOURCE tmp/sakila-db/sakila-data.sql;
        USE sakila;
        exit
    """
    # writing the sysbench file
    file_content5 = """#!/bin/bash
        # sysbench installation
        yes | sudo apt-get install sysbench
        # sysbench
        sysbench oltp_read_write --table-size=1000000 --mysql-host=127.0.0.1 --mysql-db=sakila --mysql-user=root --mysql-password=mypassword prepare
        sysbench oltp_read_write --table-size=1000000 --mysql-host=127.0.0.1 --mysql-db=sakila --mysql-user=root --mysql-password=mypassword run > results.txt
    """

    command = f"""
        cat << EOF > {file_path}
        {file_content}
    """
    command2 = f"""
        cat << EOF > {file_path2}
        {file_content2}
    """
    command3 = f"""
        cat << EOF > {file_path3}
        {file_content3}
    """
    command4 = f"""
        cat << EOF > {file_path4}
        {file_content4}
    """
    command5 = f"""
        cat << EOF > {file_path5}
        {file_content5}
    """

    accessRightChanges = """#!/bin/bash
        sudo chmod 777 write_config.sh\n
        sudo chmod 777 mysql_setup.sh\n
        sudo chmod 777 mysql_execution.sh\n
        sudo chmod 777 mysql_execution2.sh\n
        sudo chmod 777 sysbench.sh
    """

    configFile = send_command(client, command)
    mysqlFile = send_command(client, command2)
    mysqlExecutionFile = send_command(client, command3)
    sysbenchSetup = send_command(client, command4)
    mysqlExecutionFile2 = send_command(client, command5)
    accessRights = send_command(client, accessRightChanges)

    client.close()

def createNodeFile(ip, client, accesKey, privateipMaster):
    """
        Creating a file on each data node that contains all the required commands to start the data nodes
        Also changing access rights to rwx of the created file
        ----------
        ip : str
            ip adress of the instance we wish to connect to
        client : client
            paramiko client to ssh into instance
        accesKey : str
            private accesskey to gain access to instance
        privateipMaster : str
            private ip adress of the master instance needed for the connection
    """

    try:
        client.connect(hostname=ip, username="ubuntu", pkey=accesKey)
    except:
        print("could not connect to client")

    file_path = "connection.sh"

    file_content = """#!/bin/bash
    source /etc/profile.d/mysqlc.sh
    sudo apt -y install libncurses5
    cd /opt
    sudo chmod -R 777 /opt
    sudo /opt/mysqlcluster/home/mysqlc/bin/ndbd -c \"""" + str(privateipMaster) + """:1186\" """

    command = f"""
        cat << EOF > {file_path}
        {file_content}
    """

    accessRightChanges = """#!/bin/bash
        sudo chmod 777 connection.sh\n
    """

    connectionSetup = send_command(client, command)
    accessRights = send_command(client, accessRightChanges)

    client.close()

def executeFiles(ip, client, accesKey, filename):
    """
        Function to execute files on the instances to setup the cluster
        ----------
        ip : str
            ip adress of the instance we wish to connect to
        client : client
            paramiko client to ssh into instance
        accesKey : str
            private accesskey to gain access to instance
        filename : str
            name of the file which is executed on an instance
    """

    try:
        client.connect(hostname=ip, username="ubuntu", pkey=accesKey)
    except:
        print("could not connect to client")

    command = """/bin/bash """ + filename

    configSetup = send_command(client, command)

    client.close()

def main():
    """
        main function for performing the application

        Conncets to the boto3 clients
        calls the required functions

    """
    """------------Get necesarry clients from boto3------------------------"""
    ec2_client = boto3.client("ec2")
    ec2 = boto3.resource('ec2')

    """------------Create Paramiko Client------------------------------"""
    paramiko_client, accesKey = getParamikoClient()

    """-------------------Create security group--------------------------"""
    SECURITY_GROUP, vpc_id = createSecurityGroup(ec2_client)
    print("security_group: ", SECURITY_GROUP)
    print("vpc_id: ", str(vpc_id), "\n")

    """-------------------Get availability Zones--------------------------"""
    availabilityZones = getAvailabilityZones(ec2_client)
    print("Availability zones:")
    print("Zone 1a: ", availabilityZones.get('us-east-1a'), "\n")

    """-------------------Create the stand-alone instance--------------------------"""
    ins_standalone = createInstances(ec2_client, ec2, SECURITY_GROUP, availabilityZones, userdata_standalone)
    print("Instance ids: \n", str(ins_standalone[0]), "\n")
    print("Instance ip: \n", str(ins_standalone[1]), "\n")

    """-------------------Create the cluster instances--------------------------"""
    ins_cluster1 = createInstances(ec2_client, ec2, SECURITY_GROUP, availabilityZones, userdata_masternode)
    print("Instance ids: \n", str(ins_cluster1), "\n")
    print("Instance ip - master: \n", str(ins_cluster1), "\n")
    ins_cluster2 = createInstances(ec2_client, ec2, SECURITY_GROUP, availabilityZones, userdata_nodes)
    print("Instance ids: \n", str(ins_cluster2), "\n")
    print("Instance ip - node1: \n", str(ins_cluster2), "\n")
    ins_cluster3 = createInstances(ec2_client, ec2, SECURITY_GROUP, availabilityZones, userdata_nodes)
    print("Instance ids: \n", str(ins_cluster3), "\n")
    print("Instance ip - node2: \n", str(ins_cluster3), "\n")
    ins_cluster4 = createInstances(ec2_client, ec2, SECURITY_GROUP, availabilityZones, userdata_nodes)
    print("Instance ids: \n", str(ins_cluster4), "\n")
    print("Instance ip - node3: \n", str(ins_cluster4), "\n")

    """-------------------Create setup files and execute them--------------------------"""

    print("-------------------Wait installation 4 min-------------------")
    time.sleep(240)
    print("-------------------Setup file creation-------------------")
    createMasterFiles(ins_cluster1[1], paramiko_client, accesKey, str(ins_cluster1[2]), str(ins_cluster2[2]), str(ins_cluster3[2]), str(ins_cluster4[2]))
    createNodeFile(ins_cluster2[1], paramiko_client, accesKey, str(ins_cluster1[2]))
    createNodeFile(ins_cluster3[1], paramiko_client, accesKey, str(ins_cluster1[2]))
    createNodeFile(ins_cluster4[1], paramiko_client, accesKey, str(ins_cluster1[2]))
    print("-------------------files created - execute master setup-------------------")
    executeFiles(ins_cluster1[1], paramiko_client, accesKey, "write_config.sh")
    executeFiles(ins_cluster1[1], paramiko_client, accesKey, "mysql_setup.sh")
    print("-------------------node connections-------------------")
    executeFiles(ins_cluster2[1], paramiko_client, accesKey, "connection.sh")
    executeFiles(ins_cluster3[1], paramiko_client, accesKey, "connection.sh")
    executeFiles(ins_cluster4[1], paramiko_client, accesKey, "connection.sh")
    print("-------------------Connect to the cluster " + ins_cluster1[1] + " and run commands on mysql connection, sakila and sysbench manually-------------------")
    print("/bin/bash mysql_execution.sh")
    print("/bin/bash mysql_execution2.sh")
    print("/bin/bash sysbench.sh")
    print("cat results.txt")
    print("-------------------Connect to the stand-alone  " + ins_standalone[1] + " and run command to get the results-------------------")
    print("cat results.txt")

main()