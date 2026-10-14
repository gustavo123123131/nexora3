import random
import string
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
NOMES_FILE = BASE_DIR / "nomes.txt"

def get_random_identity():
    """
    Lê um nome aleatório do arquivo e gera credenciais.
    """
    real_name = "Usuario Teste"
    if NOMES_FILE.exists():
        with open(NOMES_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
            if lines:
                real_name = random.choice(lines)
    
    username = generate_username_from_name(real_name)
    password = generate_password()
    
    return {
        "real_name": real_name,
        "username": username,
        "password": password
    }

def remove_accents(input_str):
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])

def generate_username_from_name(full_name: str) -> str:
    """
    Gera user de 10 a 12 chars baseado no nome.
    Ex: Joao Pedro -> J0aoP3dr099
    """
    # 1. Limpar acentos e espaços
    clean = remove_accents(full_name)
    parts = clean.split()
    
    # Pega primeiro nome e partes do sobrenome
    base = parts[0]
    if len(parts) > 1:
        base += parts[-1]
    
    # 2. Leetspeak mapping (troca letras por numeros parecidos)
    replacements = {'a': '4', 'e': '3', 'i': '1', 'o': '0', 's': '5'}
    
    user_list = []
    for char in base:
        if char.lower() in replacements and random.random() > 0.5:
            user_list.append(replacements[char.lower()])
        else:
            user_list.append(char)
            
    user_str = "".join(user_list)
    
    # 3. Ajuste de tamanho (Min 10, Max 12)
    # Se for maior que 12, corta
    if len(user_str) > 12:
        user_str = user_str[:12]
    
    # Se for menor que 10, completa com numeros aleatorios
    while len(user_str) < 10:
        user_str += str(random.randint(0, 9))
        
    # Assegura que tenha letras e numeros (regra comum)
    if user_str.isalpha(): 
        user_str = user_str[:-2] + str(random.randint(10, 99))
        
    return user_str

def generate_password():
    """Gera senha forte: Letra Maiuscula, minuscula e numeros"""
    chars = string.ascii_letters + string.digits
    pwd = ''.join(random.choices(chars, k=10))
    # Garante requisitos minimos inserindo na força
    pwd = 'A' + pwd[1:] + '1'
    return pwd