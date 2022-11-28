# Keys are defined in configuration file
# MAKE SURE YOU UPDATED YOUR .AWS/credentials file
# MAKE SURE boto3 and matplotlib are all installed using pip
import boto3
import time
from datetime import date
from datetime import datetime, timedelta

import paramiko
import matplotlib.pyplot as plt
import matplotlib as mpl
# import webbrowser

# allows us to geth the path for the pem file
from pathlib import Path

def get_project_root() -> Path:
    """
    Function for getting the path where the program is executed
    @ return: returns the parent path of the path were the program is executed
    """
    return Path(__file__).parent

# This makes the plots made by the script open in a webbrowser
# mpl.use('WebAgg')

"""
The user data constants are used to setup and download programs on the instances
They are passed as arguments in the create instance step
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
sudo sysbench oltp_read_write --table-size=1000000 --mysql-db=sakila --mysql-user=root --mysql-password=mypassword run

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
        IpPermissions=[{
            'FromPort': 22,
            'ToPort': 22,
            'IpProtocol': 'tcp',
            'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
        },
        {
            'FromPort': 80,
            'ToPort': 80,
            'IpProtocol': 'tcp',
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
            containg instance id and ip
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
    print(ip)

    # Wait for all instances to be active!
    instance_running_waiter = ec2_client.get_waiter('instance_running')
    instance_running_waiter.wait(InstanceIds=(instance_ids))

    return [instance_ids, ip]


def main():
    """
        main function fer performing the application

        Conncets to the boto3 clients
        calls the required functions

        """
    """------------Get necesarry clients from boto3------------------------"""
    ec2_client = boto3.client("ec2")
    ec2 = boto3.resource('ec2')

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

main()