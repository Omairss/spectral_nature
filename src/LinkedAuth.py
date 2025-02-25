import json
import os

def get_creds(name='test'):
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

