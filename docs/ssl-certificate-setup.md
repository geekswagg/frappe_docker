# Installing SSL certificate on your erpnext instance

You will need to have SSL certificate installed on your instance to keep users of your system safe online. In Frappe Framework or ERPNext, this is a simple process.

Prerequisites

1. You need to have a DNS Multitenant Setup
2. Your site should be accessible via a valid domain
3. You need root permissions on your server
4. You need a valid certificate generated through a trusted Certificate Authority or a Self-Signed Certificate.

## Step 1: Install snapd on the server

    ```bash
    sudo apt update # Update the package list
    sudo apt install snapd # Install snapd
    sudo snap install core; sudo snap refresh core # Install core
    ```

## Step 2: Install Certbot

    ```bash
    sudo apt-get remove certbot # Remove any existing certbot installation
    sudo snap install --classic certbot # Install Certbot
    sudo ln -s /snap/bin/certbot /usr/bin/certbot # Create a symbolic link
    ```

for one-step autotomatic certificate installation, run the following command:

    ```bash
    sudo certbot --nginx # Generate a certificate
    ```

If you prefer manual installation, run the following command:

    ```bash
    sudo certbot certonly --nginx # Generate a certificate manually
    ```
Certbot packages on your system come with a cron job or systemd timer that will renew your certificates automatically before they expire. So no further steps are required. If necessary, you can test automatic renewal for your certificates by running this command:

        ```bash
        sudo certbot renew --dry-run
        ```

## Conclusion

You have successfully installed an SSL certificate on your ERPNext instance. Your site is now secure and users can access it safely.
[]: # (end)