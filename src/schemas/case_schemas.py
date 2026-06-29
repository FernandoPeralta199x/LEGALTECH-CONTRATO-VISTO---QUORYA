from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

class CaseCreate(BaseModel):
    """Schema para criar caso"""
    client_id: str
    case_type: str = Field(..., regex="^(due_diligence_party|due_diligence_asset|contract_analysis)$")
    description: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "client_id": "550e8400-e29b-41d4-a716-446655440000",
                "case_type": "contract_analysis",
                "description": "Análise de contrato de compra e venda"
            }
        }

class CaseResponse(BaseModel):
    """Schema de resposta de caso"""
    id: str
    client_id: str
    case_type: str
    status: str
    created_at: str

class CaseUpdate(BaseModel):
    """Schema para atualizar caso"""
    status: Optional[str] = Field(None, regex="^(open|in_progress|completed|closed)$")
    description: Optional[str] = None

class CaseResultCreate(BaseModel):
    """Schema para criar resultado de análise"""
    case_id: str
    result_type: str = Field(..., description="Ex: due_diligence, risk_analysis")
    findings: Dict[str, Any] = Field(..., description="Dados da análise em JSON")
    risk_level: str = Field(..., regex="^(low|medium|high|critical)$")
    summary_text: Optional[str] = None
    recommendations: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "case_id": "550e8400-e29b-41d4-a716-446655440000",
                "result_type": "due_diligence",
                "findings": {
                    "company_status": "active",
                    "debts": 0,
                    "legal_issues": []
                },
                "risk_level": "low",
                "summary_text": "Empresa sem riscos detectados",
                "recommendations": "Prosseguir com operação"
            }
        }

class CaseResultResponse(BaseModel):
    """Schema de resposta de resultado"""
    id: str
    case_id: str
    result_type: str
    findings: Dict[str, Any]
    risk_level: str
    status: str
    created_at: str
