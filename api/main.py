# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

# TODO: add method descriptions

import re
import json
import struct
import uvicorn
import socketio
from redis import Redis
from shared import settings
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

redis = Redis(settings.REDIS_HOST)

class Broadcast(BaseModel):
    data : str

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['GET', 'POST'])
app.add_middleware(ProxyHeadersMiddleware)

mgr = socketio.AsyncRedisManager('redis://%s' % settings.REDIS_HOST)
sio = socketio.AsyncServer(async_mode='asgi', client_manager=mgr)
app_sio = socketio.ASGIApp(sio, app)

from shared.models import Address, Transaction, Block, Utxo, WalletGroup, WalletGroupAddress
from shared.settings import POOLS
from peewee import RawQuery, fn
from datetime import datetime, timedelta

@sio.on('subscribe')
async def subscribe(sid, room):
    sio.enter_room(sid, room)

def get_latest_block():
    return Block.select().order_by(Block.height.desc()).limit(1)[0]

def get_confirmations(height, block=None):
    if height is None:
        return -1
    if block:
        b = block
    else:
        b = get_latest_block()
    return b.height - height

def tx_to_json(tx):
    is_coinbase = (len(tx.vin) == 1 and 'coinbase' in tx.vin[0])
    return {
        'blockhash': tx.block,
        'blockheight': tx.block_height,
        'timestamp': int(tx.timestamp.timestamp()),
        'confirmations': get_confirmations(tx.block_height),
        'isCoinBase': is_coinbase,
        'txid': tx.txid,
        'valueOut': tx.output_value,
        'vin': tx.vin,
        'vout': tx.addresses_out,
        'addresses_in': tx.addresses_in,
        'addresses_out': tx.addresses_out,
    }

@app.get('/richlist')
def read_richlist(order=None):
    q = Address.select()
    if order == 'sent':
        q = q.order_by(Address.sent.desc())
    elif order == 'received':
        q = q.order_by(Address.received.desc())
    else:
        q = q.order_by(Address.balance.desc())
    res = q.limit(100)
    return list(map(lambda addr : addr.to_dict(), res))

def _utxo_map(block):
    def _func(utxo):
        return {
            'txid': utxo.txid,
            'vout': int(utxo.vout),
            'amount': utxo.amount,
            'scriptPubKey': utxo.scriptPubKey,
            'address': utxo.address,
            'confirmations': get_confirmations(utxo.block_height, block=block),
        }
    return _func

### Address section

@app.get('/addr/{address}')
async def read_address(address : str):
    # Get balance info
    res = await read_address_balance(address)
    # Get wallet info
    try:
        record = WalletGroupAddress.get(WalletGroupAddress.address == address)
    except WalletGroupAddress.DoesNotExist:
        pass
    else:
        res['wallet'] = record.wallet

    return res

@app.get('/addr/{address}/balance')
async def read_address_balance(address : str):
    try:
        record = Address.get(address=address)
    except:
        return HTMLResponse(status_code=404)
    res = record.to_dict()
    unconfirmed = 0
    utxos = Utxo.select().where((Utxo.address == address) & (Utxo.spent == True)).execute()
    for utxo in utxos:
        unconfirmed += utxo.amount
    res['balance'] -= unconfirmed
    res['unconfirmed'] = unconfirmed
    return res

@app.get('/addr/{address}/utxo')
async def read_addr_utxos(address : str):
    utxos = Utxo.select().where((Utxo.address == address) & (Utxo.spent == False))
    block = get_latest_block()
    return list(map(_utxo_map(block), utxos))

@app.get('/addrs/{addresses}/utxo')
async def read_addrs_utxo(addresses : str):
    utxos = Utxo.select().where(Utxo.address.in_(addresses.split(',')))
    block = get_latest_block()
    return list(map(_utxo_map(block), utxos))


@app.get('/wallet_groups')
async def read_wallet_groups():
    wallets =(WalletGroupAddress
        .select(WalletGroupAddress.wallet, fn.COUNT(WalletGroupAddress.address))
        .group_by(WalletGroupAddress.wallet)
        .order_by(fn.COUNT(WalletGroupAddress.address).desc())
        .paginate(0, 10))

    return list(map(lambda wallet : {'wallet': wallet.wallet, 'address_count': wallet.count}, wallets))

@app.get('/wallet_groups/{uid}')
async def read_wallet_groups_uid(uid : str):
    addresses = (WalletGroupAddress
        .select(WalletGroupAddress.address, Address.balance)
        .join(Address, on=(WalletGroupAddress.address == Address.address))
        .where(WalletGroupAddress.wallet == uid)
        .order_by(Address.balance.desc())).dicts()
    # print(addresses.dicts())
    addresses = list(map(lambda address: {'address': address['address'], 'balance': address['balance']}, addresses))
    return {
        'count': len(addresses),
        'addresses': addresses
    }


@app.get('/wallet_groups/addr/{addr}')
async def read_wallet_groups_addr(addr : str):
    try:
        record = WalletGroupAddress.get(WalletGroupAddress.address == addr)
    except WalletGroupAddress.DoesNotExist:
        return HTMLResponse(status_code=404)
    return {
        'wallet': record.wallet
    }

### Transaction section

@app.get('/tx/{txid}')
def read_tx(txid : str):
    try:
        record = Transaction.get(txid=txid)
    except:
        return HTMLResponse(status_code=404)
    fee = record.input_value - record.output_value
    if fee < 0:
        fee = 0 # probably coinbase tx
    return tx_to_json(record)

@app.get('/txs/{address}')
def read_address_txs(address, beforeTime=None):
    val = re.search('^[A-Za-z0-9]+$', address)
    if not val:
        return HTMLResponse(status_code=400)
    if not beforeTime:
        beforeTime = datetime.now().timestamp()

    query = "SELECT * FROM transaction WHERE (addresses_out ? %s OR addresses_in ? %s) AND timestamp < to_timestamp(%s) ORDER BY timestamp DESC LIMIT 10"
    txs = Transaction.raw(query, address, address, beforeTime)

    txs = list(map(lambda tx: tx_to_json(tx), txs))
    if len(txs) == 0:
        lastTime = None
    else:
        lastTime = txs[-1]['timestamp']
    res = {
        'count': len(txs),
        'lastTime': lastTime,
        'txs': txs,
    }
    return res

@app.get('/txs')
def read_block_txs(block : str):
    try:
        b = Block.get(Block.hash == block)
    except Block.DoesNotExist:
        return HTMLResponse(status_code=404)
    res = {
        'txs': []
    }

    txs = Transaction.select().where(Transaction.txid.in_(b.tx))
    block = get_latest_block()
    for tx in txs:
        is_coinbase = (len(tx.vin) == 0)
        res['txs'].append({
            'blockhash': b.hash,
            'blockheight': b.height,
            'blocktime': int(b.timestamp.timestamp()),
            'confirmations': get_confirmations(b.height, block=block),
            'isCoinBase': is_coinbase,
            'txid': tx.txid,
            'valueOut': tx.output_value,
            'vin': tx.vin,
            'vout': tx.vout,
        })
        return res

### Block section

@app.get('/block/{blockhash}')
def read_blockhash(blockhash):
    prev = None
    nxt = None
    try:
        b = Block.get(Block.hash == blockhash)
        if b.height > 0:
            prev = Block.get(Block.height == b.height - 1)
    except Block.DoesNotExist:
        return HTMLResponse(status_code=404)
    try:
        nxt = Block.get(Block.height == b.height + 1, Block.orphaned == False)
    except Block.DoesNotExist:
        pass
    txs = list(Transaction.select().where(Transaction.block == b.hash).execute())

    txs = list(map(lambda tx : {
        'txid': tx.txid,
        'timestamp': int(tx.timestamp.timestamp()),
        'addresses_in': tx.addresses_in,
        'addresses_out': tx.addresses_out
    }, txs))

    def func(a):
        return 1 if 'null' in a['addresses_in'] else 0

    txs.sort(key=func, reverse=True)

    pool = None
    cb = bytes(b.coinbase)
    for key, value in POOLS.items():
        if cb.find(key.encode()) != -1:
            pool = value
    res = {
        'height': b.height,
        'hash': b.hash,
        'timestamp': int(b.timestamp.timestamp()),
        'merkleroot': b.merkle_root,
        'txs': txs,
        'difficulty': b.difficulty,
        'size': b.size,
        'version_hex': bytes(b.version).hex(),
        'version': struct.unpack('i', bytes(b.version))[0],
        'bits': bytes(b.bits).hex(),
        'nonce': b.nonce,
        'pool': pool,
    }
    if prev:
        res['previousblockhash'] = prev.hash
    if nxt:
        res['nextblockhash'] = nxt.hash
    return res

@app.get('/blocks')
def read_blocks(beforeBlock=None,  limit : int = 100):
    q = Block.select()
    if beforeBlock:
        q = q.where(Block.height < beforeBlock)
    if limit > 100:
        limit = 100
    blocks = q.order_by(Block.timestamp.desc()).limit(limit)
    res = []
    for b in blocks:
        pool = None
        cb = bytes(b.coinbase)
        for key, value in POOLS.items():
            if cb.find(key.encode()) != -1:
                pool = value
        res.append({
            'height': b.height,
            'hash': b.hash,
            'timestamp': int(b.timestamp.timestamp()),
            'merkle_root': b.merkle_root,
            'tx': b.tx,
            'difficulty': b.difficulty,
            'size': b.size,
            'version_hex': bytes(b.version).hex(),
            'version': struct.unpack('i', bytes(b.version))[0],
            'bits': bytes(b.bits).hex(),
            'nonce': b.nonce,
            'pool': pool
        })
    return res

### Misc

@app.get('/misc')
def read_misc():
    supply = Address.select(fn.SUM(Address.balance)).execute(None)[0].sum

    return {
        'supply': int(supply),
    }

# TODO: Probably should cache this method
@app.get('/distribution')
def read_distribution():
    supply = Address.select(fn.SUM(Address.balance)).execute(None)[0].sum

    res = {}
    sq = Address.select(Address.balance).order_by(Address.balance.desc()).limit(25)
    q = Address.select(fn.SUM(sq.c.balance)).from_(sq)

    res['0_24'] = {
        'percent': (q[0].sum / supply) * 100,
        'total': q[0].sum
    }
    sq = Address.select(Address.balance).order_by(Address.balance.desc()).offset(25).limit(25)
    q = Address.select(fn.SUM(sq.c.balance)).from_(sq)
    res['25_49'] = {
        'percent': (q[0].sum / supply) * 100,
        'total': q[0].sum
    }
    sq = Address.select(Address.balance).order_by(Address.balance.desc()).offset(50).limit(25)
    q = Address.select(fn.SUM(sq.c.balance)).from_(sq)
    res['50_99'] = {
        'percent': (q[0].sum / supply) * 100,
        'total': q[0].sum
    }
    sq = Address.select(Address.balance).order_by(Address.balance.desc()).offset(100)
    q = Address.select(fn.SUM(sq.c.balance)).from_(sq)
    res['remain'] = {
        'percent': (q[0].sum / supply) * 100,
        'total': q[0].sum
    }

    return res

@app.get('/mempool')
def read_mempool():
    txs = Transaction.select().where(Transaction.block == None)
    data = []
    for record in txs:
        fee = record.input_value - record.output_value
        if fee < 0:
            fee = 0 # probably coinbase tx
        data.append({
            'txid': record.txid,
            'block': record.block,
            'timestamp': record.timestamp.timestamp(),
            'input_value': record.input_value,
            'output_value': record.output_value,
            'fee': fee,
            'addresses_in': record.addresses_in,
            'addresses_out': record.addresses_out,
        })
    return data

@app.get('/status')
def read_status(q=None):
    if q == 'getInfo':
        latest_block = Block.select().order_by(Block.height.desc()).get()
        mempool_txs = Transaction.select().where(Transaction.block == None).count()
        return {
            'blocks': latest_block.height,
            'lastblockhash': latest_block.hash,
            'difficulty': latest_block.difficulty,
            'mempool_txs': mempool_txs,
        }
    elif q == 'getBestBlockHash':
        latest_block = Block.select(Block.hash).where(Block.orphaned == False).order_by(Block.height.desc()).get()
        return {
            'bestblockhash': latest_block.hash
        }
    elif q == 'getDifficulty':
        latest_block = Block.select(Block.difficulty).where(Block.orphaned == False).order_by(Block.height.desc()).get()
        return {
            'difficulty': latest_block.difficulty
        }
    elif q == 'getLastBlockHash':
        latest_block = Block.select(Block.hash).where(Block.orphaned == False).order_by(Block.height.desc()).get()
        return {
            'syncTipHash': latest_block.hash,
            'lastblockhash': latest_block.hash,
        }

@app.post('/broadcast')
def broadcast(data : Broadcast):
    redis.publish('broadcast', data.data)
    return data
