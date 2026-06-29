from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional

class ClientCreateSchema(BaseModel):
    """Schema para criar cliente"""
    name: str = Field(..., min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    cpf_cnpj: str = Field(..., regex=r"^\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}$|^\d{3}\.\d{3}\.\d{3}-\d{2}$")
    type: str = Field(..., pattern="^(pf|pj)$")
    
    @validator('cpf_cnpj')
    def validate_cpf_cnpj(cls, v):
        # Remover formatação
        clean = v.replace('.', '').replace('/', '').replace('-', '')
        
        # Validar comprimento
        if len(clean) not in [11, 14]:
            raise ValueError('CPF/CNPJ inválido')
        
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "Empresa XYZ Ltda",
                "email": "contato@xyz.com",
                "phone": "(11) 99999-9999",
                "cpf_cnpj": "12.345.678/0001-90",
                "type": "pj"
            }
        }

class ClientUpdateSchema(BaseModel):
    """Schema para atualizar cliente"""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)
    type: Optional[str] = Field(None, pattern="^(pf|pj)$")