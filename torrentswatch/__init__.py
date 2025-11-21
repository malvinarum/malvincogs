import json
from pathlib import Path
from .torrentswatch import TorrentsWatch

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = json.load(fp).get("end_user_data_statement", "")

async def setup(bot):
    await bot.add_cog(TorrentsWatch(bot))