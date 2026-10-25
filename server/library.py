"""Browser-owned library selections, validated and frozen into each task."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class ProductSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    facts: str = Field(min_length=1, max_length=6000)
    source: str = Field(min_length=1, max_length=500)

class AreaSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    radius_m: int = Field(gt=0, le=3000)

class SkillSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=4000)
    stages: list[Literal['L0','L1','L1D','L2','L2C','L3','L3P','L4','PACK']] = Field(min_length=1,max_length=9)

class LibrarySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: ProductSelection | None = None
    area: AreaSelection | None = None
    skills: list[SkillSelection] = Field(default_factory=list,max_length=10)


def apply_selection(inputs, selection):
    if not selection: return
    selection = LibrarySelection.model_validate(selection).model_dump()
    inputs['library_selection'] = selection
    inputs['skills'] = selection['skills']
    product = selection.get('product')
    if product:
        profile = inputs['profile']
        profile['product'] = {'name':product['name'], 'specification':{'facts':[product['facts']], 'source':'library-product'}}
        for point in profile['selling_points']:
            point.update(fact=product['facts'], name=product['name'], source='library-product')
        profile['material'].update(pitch_template='您好，向您介绍{product}。产品资料：{fact}。希望先了解贵店的客户需求，再讨论合作条件。',
                                  table_copy='{product}：欢迎了解产品资料与合作条件。',
                                  angles={'补位':'先了解尚未满足的需求，再讨论补充陈列。','正面':'围绕本品资料介绍特点，邀请核对样品与进货条件。','避让':'先做需求访谈并记录后续跟进条件，暂不推动替换现有陈列。'})
        inputs['library_documents'] = {'library-product': product['facts']+'\n资料来源：'+product['source']}
    if selection.get('area'):
        inputs['site']['display_name'] = selection['area']['name']
        inputs['coverage']['site']['display_name'] = selection['area']['name']
