#!/usr/bin/python
#------------------------------------------------------------------------------
# The MIT License (MIT)

# Copyright (c) 2014 Jordi Arnavat

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#------------------------------------------------------------------------------
import boto
import boto.ec2
from boto.exception import EC2ResponseError
import time
import subprocess
import urllib2
import re
import argparse
import os.path
import sys
import shutil
import logging

SCRIPT_NAME = 'AWSiRecovery'

logger = logging.getLogger(SCRIPT_NAME)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
ch.setFormatter(formatter)

if not os.path.exists('logs'):
	os.makedirs('logs')	

fh = logging.FileHandler('logs/awsirecovery.log')
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)

logger.addHandler(ch)
logger.addHandler(fh)

def getMyIP():
  request =  urllib2.Request('http://whatsmyip.net/') 
  #ie7 user-agent just in case...
  request.add_header('User-Agent','Mozilla/5.0 (Windows; U; MSIE 7.0; Windows NT 6.0; en-US)')
  opener = urllib2.build_opener()  
  return re.findall( r'[0-9]+(?:\.[0-9]+){3}', opener.open(request).read() )[0]

class AwsWrapper(object):

	def __init__(self, region):
		self.conn = boto.ec2.connect_to_region(region)


class AwsWrapperExtended(AwsWrapper):
	parameter = 'state'

	def waitUntil(self, object , state):
		while getattr(object,self.parameter) != state:
			time.sleep(8)
			object.update()
			logger.debug('%s: %s'%(object, getattr(object,self.parameter)))

class Instance(AwsWrapperExtended):

	def __init__(self,id=None,name=None,region='eu-west-1'):
		super(Instance, self).__init__(region)
		if id:
			self.get(id)

	def get(self,id):
		self.instance = self.conn.get_all_instances(instance_ids=id)[0].instances[0]

	def waitUntil(self,state):
		super(Instance, self).waitUntil(self.instance,state)	

	def create(self, ami_id , tags={} , **kwargs):
		try:
			reservation = self.conn.run_instances(ami_id,**kwargs)
		except EC2ResponseError, err:
			logger.error('Error creating instance: %s'%str(err))
			return

		self.instance = reservation.instances[0]
		self.waitUntil('running')

		for name, value in tags:
			self.instance.add_tag(tag, value)

	def stop(self):
		self.instance.stop()
		self.waitUntil('stopped')

	def terminate(self):
		self.conn.terminate_instances(instance_ids=[self.instance.id])
		self.waitUntil('terminated')
		del(self.instance)

	def getMappedDevices(self):
		return self.instance.block_device_mapping.keys()

	def getVolume(self, mountpoint):
		return Volume(self.instance.block_device_mapping.get(mountpoint).volume_id)


class Volume(AwsWrapperExtended):
	parameter = 'status'

	def __init__(self,id,region='eu-west-1'):
		super(Volume, self).__init__(region)
		self.get(id)

	def waitUntil(self,state):
		super(Volume, self).waitUntil(self.volume,state)

	def get(self,id):
		self.volume = self.conn.get_all_volumes(volume_ids=id)[0]

	def attach(self,instance_id, mountpoint):
		logger.info("Attaching volume %s from instance %s"%(self.volume.id,instance_id))
		self.conn.attach_volume(self.volume.id, instance_id = instance_id, device=mountpoint)
		self.waitUntil("in-use")

	def detach(self,instance_id, mountpoint):
		logger.info("Detaching volume %s from instance %s"%(self.volume.id,instance_id))
		self.conn.detach_volume(self.volume.id, instance_id = instance_id, device=mountpoint)
		self.waitUntil("available")

class RecoverySecurityGroup(AwsWrapper):

	def __init__(self,name=None,region='eu-west-1'):
		super(RecoverySecurityGroup, self).__init__(region)
		self.name = '%s:SecurityGroup'%SCRIPT_NAME
		if name:
			self.name = name	

	def create(self):
		try:
			sg = self.conn.get_all_security_groups(groupnames=[self.name])
			self.securityGroup = sg[0]
		except:
			logger.info('Creating temporary security group for rescuing')
			self.securityGroup = self.conn.create_security_group(self.name, self.name)
			self.securityGroup.authorize('tcp', 22, 22, getMyIP()+'/32')

	def delete(self):	
		try:
			logger.info('Deleting %s'%self.securityGroup)
			self.conn.delete_security_group(name=self.securityGroup.name, group_id= self.securityGroup.id)
		except EC2ResponseError, err:
			logger.warning('Error deleting %s: %s'%(self.securityGroup,str(err)))

def recoverInstance(instance_id, keyname, keypair):
	sg = RecoverySecurityGroup()
	sg.create()

	logger.info('Getting instance:%s to recover'%instance_id)
	instance_to_recover = Instance(id=instance_id)

	recoverInstance = Instance()
	logger.info('Creating temporary instance')
	recoverInstance.create('ami-892fe1fe',
							key_name = keyname,
							instance_type = 't2.micro',
							security_group_ids =[sg.securityGroup.id],
							subnet_id = instance_to_recover.instance.subnet_id )

	logger.info('Stopping instance:%s'%instance_id)
	instance_to_recover.stop()

	mountpoint = instance_to_recover.getMappedDevices()[0]
	volume = instance_to_recover.getVolume(mountpoint)
	volume.detach(instance_to_recover.instance.id, mountpoint)
	volume.attach(recoverInstance.instance.id,'/dev/sdh')
	
	executePlaybook(os.path.join('playbooks','recover_instance.yml'),recoverInstance.instance, keypair,'ec2-user')
	
	recoverInstance.stop()
	volume.detach(recoverInstance.instance.id,'/dev/sdh')
	volume.attach(instance_to_recover.instance.id, mountpoint)
	recoverInstance.terminate()
	sg.delete()

def executePlaybook(playbook, instance, private_key, remote_user):
	ansible_cmd = 'ansible-playbook -i %s, %s --private-key=%s -u %s'%(instance.public_dns_name,playbook,private_key,remote_user)
	subprocess.call('ssh-keyscan -t rsa %s >> ~/.ssh/known_hosts'%instance.public_dns_name, shell=True)
	logger.info("Executing Ansible: %s"%ansible_cmd)
	subprocess.call(ansible_cmd, shell=True)
	subprocess.call('ssh-keygen -R %s'%instance.public_dns_name, shell=True)
	subprocess.call('ssh-keygen -R %s'%instance.ip_address, shell=True)

def test(keyname, keypair):
	sg = RecoverySecurityGroup(name="testInstanceSecurityGroup")
	sg.create()
	testInstance = Instance()
	testInstance.create('ami-892fe1fe',
						key_name = keyname,
						instance_type = 't2.micro',
						security_group_ids=[sg.securityGroup.id])

	recoverInstance(testInstance.instance.id, keyname, keypair)
	testInstance.terminate()
	sg.delete()

def checkIfIsFile(args,key):
	if not os.path.isfile(getattr(args,key)):
		logger.error('File %s not found'%getattr(args,key))
		sys.exit(1)
	logger.info('File %s found'%getattr(args,key))


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(	dest='action',
    									title='Options',
	                                   	description='',
										help='additional help')

    regions = ['us-east-1','us-east-2','us-west-1','eu-west-1','ap-southeast-1','ap-southeast-2','ap-northeast-1','sa-east-1']

    recoverParser = subparsers.add_parser('recover', help='recover instance in aws')

    recoverParser.add_argument('-i', '--instance', 
    						help='You need to specify the id of the instance that you want to recover access',
    						required=True)
    recoverParser.add_argument('-n', '--keyname', 
    						help='Amazon Keypair name that will use to create a temporary instance in order to recover the instance which the one you have lost access',
    						required=True)
    recoverParser.add_argument('-k', '--keypair', 
    						help='Amazon Keypair that will use to create a temporary instance in order to recover the instance which the one you have lost access',
    						required=True)
    recoverParser.add_argument('-p', '--public_key', 
    						help='SSH public key for the new user that will be created in the instance to recover',
    						required=True)
    recoverParser.add_argument('-r', '--region', 
    						choices= regions,
    						default='eu-west-1')
    
    testParser = subparsers.add_parser('test', help='Test unit for the library. It will create a new temporary instance to recover in your aws account and it will grant access to it')

    testParser.add_argument('-n', '--keyname', 
    						help='Amazon Keypair name that will use to create a temporary instance in order to recover the instance which the one you have lost access',
    						required=True)
    testParser.add_argument('-k', '--keypair', 
    						help='Amazon Keypair that will use to create a temporary instance in order to recover the instance which the one you have lost access',
    						required=True)
    testParser.add_argument('-p', '--public_key', 
    						help='SSH public key for the new user that will be created in the instance to recover',
    						required=True)
    testParser.add_argument('-r', '--region', 
    						choices=regions,
    						default='eu-west-1')

    args = parser.parse_args()

    checkIfIsFile(args,'keypair')
    checkIfIsFile(args,'public_key')
   
    if not os.path.exists('tmp'):
		os.makedirs('tmp')	

    logger.info('Copying public_key [%s] into tmp/ folder'%args.public_key)
    shutil.copyfile(args.public_key,os.path.join('tmp','public_key'))

    logger.info('Executing %s'%(args.action))
    if args.action == "recover":
    	logger.info('Executing %s'%args)
    	recoverInstance(args.instance, args.keyname, args.keypair)
    elif args.action == "test":
    	logger.info('Executing %s'%args)
    	test(args.keyname, args.keypair)

    os.remove(os.path.join('tmp','public_key'))
   
if __name__ == "__main__":
   main()