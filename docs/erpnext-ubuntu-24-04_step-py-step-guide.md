# How to Install ERPNext 15 on Ubuntu 24.04

ERPNext is a powerful, open-source ERP system that streamlines business processes from inventory management to accounting. Whether you’re new to ERPNext or you have worked with it for years, this step-by-step tutorial will walk you through the installation process, ensuring you have everything set up correctly on your Ubuntu 24.04 server. The ForgeFusion platform backend service integrates with ERPNext to provide a seamless experience for your fieldforce.

In this guide, you will learn how to install ERPNext on Ubuntu 24.04 using the easy install script. This script will automatically install all the necessary dependencies and set up the ERPNext system for you. This is the first step to getting your ERP system up and running in readines for Hewani platform integration.

## Prerequisites

Before you begin, you will need the following:

- A server running Ubuntu 24.04
- A sudo user
- At least 2GB of RAM (4GB recommended)
- A fully qualified domain name (FQDN) pointing to your server’s IP address
- A valid SSL certificate for your domain (optional but recommended)
- MariaDB 10.3.x
- A cup of coffee ☕
- Node.js 18
- Python 3.11+
- pip 20+
- yarn 1.12+

## Step 1: Update Your System

Before you begin, it’s a good idea to update your system to ensure you have the latest security patches and software updates. To do this, run the following commands:

```bash
sudo apt update # Update the package list
sudo apt upgrade -y # Upgrade the installed packages
```

## Step 2: Create a new user – (bench user)

In Linux, the root user processes escalated privileges to perform any tasks within the system. This is why it is not advisable to use this user on a daily basis. We will create a user that we can use, and this will be the user we will also use as the Frappe Bench User.

```bash
sudo adduser [frappe-user] # Replace [frappe-user] with your username
usermod -aG sudo [frappe-user] # Add the user to the sudo group
su [frappe-user] # Switch to the new user
cd /home/[frappe-user] # Change to the user's home directory
``` 

Ensure you have replaced [frappe-user] with your username. eg. sudo adduser frappe

## Step 3: Install Prerequisites

Before you can install ERPNext, you need to install some prerequisites. Run the following commands to install the required packages:

```bash
sudo apt-get install git # Install Git
sudo apt-get install python3-dev # Install Python 3 development files
sudo apt-get install python3-setuptools python3-pip # Install Python 3 pip
sudo apt install python3.12-venv # Install Python 3 venv
```

## Step 4: Setup and install MariaDb

MariaDB is a popular open-source database management system that is compatible with MySQL. To install MariaDB, run the following commands:

```bash

sudo apt-get install software-properties-common  # Install software-properties-common
sudo apt install mariadb-server # Install MariaDB
sudo mysql_secure_installation # Secure MariaDB
```

When you run this command, the server will show the following prompts. Please follow the steps as shown below to complete the setup correctly.

- Enter current password for root: (Enter your SSH root user password)
- Switch to unix_socket authentication [Y/n]: Y
- Change the root password? [Y/n]: Y. It will ask you to set new MySQL root password at this step. This can be different from the SSH root user password.
- Remove anonymous users? [Y/n] Y
- Disallow root login remotely? [Y/n]: N. This is set as N because we might want to access the database from a remote server for using business analytics software like Metabase / PowerBI / Tableau, etc.
- Remove test database and access to it? [Y/n]: Y
- Reload privilege tables now? [Y/n]: Y

Edit MYSQL default config file

```bash
sudo nano /etc/mysql/my.cnf # Open the MariaDB configuration file
```

Add the following lines to the end of the file

```bash
[mysqld]
character-set-client-handshake = FALSE  # Set the character set handshake to false
character-set-server = utf8mb4 # Set the character set to utf8mb4
collation-server = utf8mb4_unicode_ci # Set the collation to utf8mb4_unicode_ci

[mysql]
default-character-set = utf8mb4 # Set the default character set to utf8mb4
```

Save and exit the file. Restart the MariaDB service to apply the changes.

```bash
sudo service mysql restart # Restart the MariaDB service
```

## Step 5: Install Redis Server

ERPNext uses Redis for caching and queuing. To install Redis, run the following commands:

```bash
sudo apt-get install redis-server # Install Redis
sudo systemctl enable redis-server # Enable Redis
sudo systemctl start redis-server # Start Redis
```

## Step 6: Install CURL, Node.js, NPM, Yarn and wkhtmltopdf

ERPNext requires Node.js to run. To install Node.js, run the following commands:

```bash
sudo apt install curl # Install Curl
curl https://raw.githubusercontent.com/creationix/nvm/master/install.sh | bash # Install NVM
source ~/.profile # Load the NVM script
nvm install 18 # Install Node.js 18
sudo apt-get install npm -y # Install NPM
sudo npm install -g yarn -y # Install Yarn
sudo apt-get install xvfb libfontconfig wkhtmltopdf -y # Install wkhtmltopdf
```

## Step 7: Install Frappe Bench

Frappe Bench is a command-line tool that helps you install and manage ERPNext.
In this step, we need to supply the – H and –break-system-packages flags. The flags do the following:

- -H: Sets the HOME environment variable to the home directory of the target user. This ensures that the package installation happens in the correct directory and avoids permission issues.
- --break-system-packages: Overrides the default behavior of pip to avoid breaking system packages. This flag is used to indicate that you are aware that this installation might cause conflicts with the system package manager, but you want to proceed anyway.

To install Frappe Bench, run the following commands:

```bash
sudo -H pip3 install frappe-bench --break-system-packages # Install Frappe Bench
sudo -H pip3 install ansible --break-system-packages # Install Ansible
bench init frappe-bench --frappe-branch version-15 # Initialize Frappe Bench
cd frappe-bench # Change to the Frappe Bench directory
chmod -R o+rx /home/[frappe-user] # Change the permissions
```

Ensure you have replaced [frappe-user] with your username. eg. sudo adduser frappe  
Ensure you have replaced [site-name] with your site name. eg. bench new-site site1.local

## Step 8: Install ERPNext and other apps

The first app we will download is the payments app. This app is required when setting up ERPNext.

```bash
bench get-app payments # Get the payments app
bench get-app --branch version-15 erpnext # Get the ERPNext app
bench get-app hrms # Get the HRMS app
bench --site [site-name] install-app erpnext # Install ERPNext
bench --site [site-name] install-app hrms # Install HRMS
bench start # Start the bench
```

Ensure you have replaced [site-name] with your site name. eg. bench --site site1.local install-app erpnext

## Step 9: Set up ERPNext production environment

To set up ERPNext for production, run the following commands:

```bash
bench --site [site-name] enable-scheduler # Enable the scheduler
bench --site [site-name] set-maintenance-mode off # Disable maintenance mode
sudo bench setup production [frappe-user] # Setup production
bench setup nginx # Setup Nginx
sudo supervisorctl restart all # Restart Supervisor
sudo bench setup production [frappe-user] # Setup production
```

Ensure you have replaced [site-name] with your site name. eg. bench --site site1.local enable-scheduler
Ensure you have replaced [frappe-user] with your username. eg. sudo

If you are prompted to save the new/existing config file, respond with a Y.

## Step 10: Access ERPNext from the browser.

To access ERPNext, open your web browser and navigate to your server’s IP address or domain name. You should see the ERPNext login page. Log in with the username and password you created during the installation process. If you hadn't configured SSL, you can access the site using http://[server-ip]:8000. For ssl configurations, you can use the [Installing SSL certificate on your erpnext instance](ssl-certificate-setup.md) guide. 

