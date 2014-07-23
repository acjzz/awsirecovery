## AWSiRescovery stands for *AWS Instance Recovery*

It is a python script that can help you to recover access to a linux instance in AWS in case you have lost access to it.

### How does it work?
1. Create a new SecurityGroup allowing SSH connection from the ip you are running the script
2. Create a new fresh instance in the same subnet than the instance to recover
3. Stop the instance to recover
4. Detach the main volume of the instance to recover
5. Attach this volume as secondary device in the fresh instance
6. Execute the playbook on the fresh instance in order to create a recovery_user in the instance to recover using chroot
7. Detach the volume
8. Attach the volume to the instance to recover
9. Terminate the fresh instance created at the beginning
10. Delete the SecurityGroup created at the beginning of the script

### Dependencies:
- [boto](https://github.com/boto/boto)
- [ansible](https://github.com/ansible/ansible)

### Getting Started

First of all, in order to use this script, you have to provide your credentials to boto. [Please follow this link to do it](https://code.google.com/p/boto/wiki/BotoConfig)

And then just run the script from the command line:
```
python awsirecovery.py recover -h
```