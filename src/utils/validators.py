"""Validadores de dados brasileiros e internacionais"""
import re
from typing import Tuple

def validate_cpf(cpf: str) -> bool:
    """Validar CPF brasileiro"""
    cpf = cpf.replace('.', '').replace('-', '').strip()
    
    if len(cpf) != 11 or not cpf.isdigit():
        return False
    
    # Rejeitar CPFs com todos os dígitos iguais
    if cpf == cpf[0] * 11:
        return False
    
    # Validar primeiro dígito verificador
    sum_1 = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digit_1 = 11 - (sum_1 % 11)
    digit_1 = 0 if digit_1 > 9 else digit_1
    
    if int(cpf[9]) != digit_1:
        return False
    
    # Validar segundo dígito verificador
    sum_2 = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digit_2 = 11 - (sum_2 % 11)
    digit_2 = 0 if digit_2 > 9 else digit_2
    
    return int(cpf[10]) == digit_2

def validate_cnpj(cnpj: str) -> bool:
    """Validar CNPJ brasileiro"""
    cnpj = cnpj.replace('.', '').replace('-', '').replace('/', '').strip()
    
    if len(cnpj) != 14 or not cnpj.isdigit():
        return False
    
    # Rejeitar CNPJs com todos os dígitos iguais
    if cnpj == cnpj[0] * 14:
        return False
    
    # Validar primeiro dígito verificador
    sum_1 = sum(int(cnpj[i]) * (5 - i % 5) for i in range(12))
    digit_1 = 11 - (sum_1 % 11)
    digit_1 = 0 if digit_1 > 9 else digit_1
    
    if int(cnpj[12]) != digit_1:
        return False
    
    # Validar segundo dígito verificador
    sum_2 = sum(int(cnpj[i]) * (6 - i % 6) for i in range(13))
    digit_2 = 11 - (sum_2 % 11)
    digit_2 = 0 if digit_2 > 9 else digit_2
    
    return int(cnpj[13]) == digit_2

def validate_email(email: str) -> bool:
    """Validar email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_password_strength(password: str) -> Tuple[bool, str]:
    """Validar força da senha"""
    if len(password) < 8:
        return False, "Senha deve ter no mínimo 8 caracteres"
    
    if not any(c.isupper() for c in password):
        return False, "Senha deve conter letras maiúsculas (A-Z)"
    
    if not any(c.islower() for c in password):
        return False, "Senha deve conter letras minúsculas (a-z)"
    
    if not any(c.isdigit() for c in password):
        return False, "Senha deve conter números (0-9)"
    
    if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in password):
        return False, "Senha deve conter caracteres especiais (!@#$%^&*)"
    
    return True, "Senha válida"

def validate_phone(phone: str) -> bool:
    """Validar telefone brasileiro"""
    phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    
    if len(phone) not in [10, 11]:
        return False
    
    if not phone.isdigit():
        return False
    
    return True

def validate_zip_code(zip_code: str) -> bool:
    """Validar CEP brasileiro"""
    zip_code = zip_code.replace('-', '').strip()
    
    if len(zip_code) != 8 or not zip_code.isdigit():
        return False
    
    return True
