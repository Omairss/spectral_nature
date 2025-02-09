import json

def get_creds(name='test'):
    file_path = f'../../secrets/{name}.json'

    with open(file_path, 'r') as file:
        data = json.load(file)
        u = data.get('u')
        p = data.get('p')
        return u, p

