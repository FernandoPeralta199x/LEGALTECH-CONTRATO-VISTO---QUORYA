from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional

class UserLoginSchema(BaseModel):
    """Schema para login"""
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "admin@test.com",
                "password": "Admin@12345"
            }
        }

class UserSignupSchema(BaseModel):
    """Schema para cadastro"""
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    name: str = Field(..., min_length=2, max_length=255)
    role: str = Field(..., pattern="^(admin|manager|analyst)$")
    
    @validator('password')
    def validate_password(cls, v):
        if not any(char.isupper() for char in v):
            raise ValueError('Senha deve conter pelo menos uma letra maiúscula')
        if not any(char.isdigit() for char in v):
            raise ValueError('Senha deve conter pelo menos um número')
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "email": "novo@email.com",
                "password": "Senha@123",
                "name": "João Silva",
                "role": "analyst"
            }
        }

class ForgotPasswordSchema(BaseModel):
    """Schema para recuperar senha"""
    email: EmailStr

class ResetPasswordSchema(BaseModel):
    """Schema para resetar senha"""
    token: str = Field(..., min_length=20)
    password: str = Field(..., min_length=6, max_length=128)
