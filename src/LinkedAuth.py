import json
import os
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential

def get_creds_old_v1(name='test'):
    current_dir = os.getcwd()
    if 'src' in current_dir.split(os.sep):
        src_index = current_dir.split(os.sep).index('src')
        if src_index == len(current_dir.split(os.sep)) - 1:
            file_path = f'../secrets/{name}.json'
        elif src_index == len(current_dir.split(os.sep)) - 2:
            file_path = f'../../secrets/{name}.json'
    
    #file_path = f'../../secrets/{name}.json'

    with open(file_path, 'r') as file:
        data = json.load(file)
        u = data.get('u')
        p = data.get('p')
        return u, p


def get_creds(KV_NAME="spectral-nature-kvault", retreive = ['rh-username', 'rh-pswd']):

    KVUri = f"https://{KV_NAME}.vault.azure.net"
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KVUri, credential=credential)

    return [client.get_secret(i).value for i in retreive]
