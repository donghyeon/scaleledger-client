# models.py
from tortoise.models import Model
from tortoise import fields


class Gateway(Model):
    id = fields.IntField()
    mac_address = fields.CharField(max_length=17, primary_key=True)
    hostname = fields.CharField(max_length=255, null=True)
    ip_address = fields.CharField(max_length=15, null=True)
    access_token = fields.CharField(max_length=64, null=True)
    name = fields.CharField(max_length=255, null=True)
    description = fields.TextField(null=True)
    status = fields.CharField(max_length=10, null=True)
    last_heartbeat = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(null=True)
    updated_at = fields.DatetimeField(null=True)

    class Meta:
        table = "gateway"

    def __repr__(self):
        return f"<Gateway(name={self.name}, mac_address={self.mac_address})>"
