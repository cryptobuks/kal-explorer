import os
import bitcointx
import importlib

from dotenv import load_dotenv
load_dotenv()

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASS = os.getenv('DB_PASS', 'postgres')
DB_PORT = os.getenv('DB_PORT', 5432)

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
# Node IP should be of a trusted node as it is relied upon for block verification
NODE_IP    = os.getenv('COIN_DAEMON', '45.77.228.139')

EXP_COIN = os.getenv('COIN', 'tux')
module = importlib.import_module("shared.coins.%s" % EXP_COIN)
ChainParams = module.ChainParams

DB_NAME = 'explorer_%s' % EXP_COIN

POOLS = module.POOLS