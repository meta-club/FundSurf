import datetime
import mango
import time
import base58
import decimal
import os
import requests

from solana.rpc.api import Client
from solana.publickey import PublicKey

from dotenv import load_dotenv
from datetime import datetime, timedelta

from supabase import create_client, Client as Cl

load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Cl = create_client(url, key)

ENV = os.environ.get("ENV")
ACCOUNT_1 = os.environ.get("ACCOUNT_1")
QUANTITY = 30 # Quantity of token to use
THRESHOLD = 30 # Delta of positions to decide a rebalance

http_client = Client("https://api.mainnet-beta.solana.com")
pwd = os.environ.get("PRIVATE_KEY")

byte_array = base58.b58decode(pwd)
keypair = list(map(lambda b: int(str(b)), byte_array))[:]
wallet = mango.Wallet(bytes(keypair))

#TODO: MAKE SURE PRICE SOLD IS FAIR, PREVENT WICKS FROM WRECKING THE POSITION


def get_funding(perp):
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:                                
        perp_market = mango.market(context, perp)
        funding = perp_market.fetch_funding(context)        
        funding = funding.extrapolated_apr * 100
        return funding


def validate_tx(orders):
    for order in orders:
        print("\n","Eval new order {}".format(order),"\n")
        url = "https://public-api.solscan.io/transaction/{}".format(order)
        headers = {'user-agent': 'my-app/0.0.1'}
        response = requests.get(url, headers=headers)
        response = response.json()        
        print("STATUS",  response["status"])
        if response["status"] == 404:
            return False        
        elif response["status"] == "Success" or response["status"] == "success"  or response["status"] == 200:
            return True
        else: 
            print("UNIDENTIFIED OPTIONS RETURN VAL")
            return False



"""
DB TABLE: 
id: int
funding: float8
curr_pos: text/varchar
created_at: datetime
"""
def track_positions(funding, curr_pos):
    print("FUNDING IS", funding)
    print("CURR POSITION IS", curr_pos)
    print("TYPE OF FUNDING", type(funding))
    funding = float(funding)
    data = supabase.table("positions").select("*").execute()    
    if len(data.data) == 0:
        supabase.table("positions").insert({"curr_pos":curr_pos, "funding": funding}).execute()
    else:
         #TODO: ID IS ARBITRARY ACCORDING TO DB TABLE
         supabase.table("positions").update({"curr_pos": curr_pos, "funding": funding}).eq("id", 3).execute()


def get_open_orders():
    data = supabase.table("positions").select("*").execute()
    print("OPEN ORDERS ARE", data)
    if len(data.data) == 0:
        return None, None    
    curr_pos = data.data[0]["curr_pos"] 
    old_apr = data.data[0]["funding"] 
    if not curr_pos:
        funding = None
        curr_pos = None
    else: 
        funding = get_funding("{}-PERP".format(curr_pos))        
        supabase.table("positions").update({"funding": float(funding)}).eq("id", 3).execute()        
    return  funding, curr_pos, old_apr
        


def query_pnl_unrealized():
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        group = mango.Group.load(context)
        cache: mango.Cache = mango.Cache.load(context, group.cache)
        account = mango.Account.load(context, PublicKey(ACCOUNT_1), group)
        open_orders = account.load_all_spot_open_orders(context)
        frame = account.to_dataframe(group, open_orders, cache)
        print("PNL OF PERP POSITIONS", frame["RedeemablePnL"][2])


def get_price(inst):    
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        market = mango.market(context, inst)
        print("PRICE", market.fetch_orderbook(context).top_bid.price)
        top_bid = market.fetch_orderbook(context).mid_price
        return top_bid


def query_apr():
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:   
        #We will focus on only high liquidity pairs for now     
        perp_pairs = [
            "SOL-PERP",
            "BTC-PERP"
        ]
        spot_pairs = [
            "SOL/USDC",
            "BTC/USDC"
        ]
        biggest_apr = 0
        biggest_perp = None
        biggest_spot_pair = None
        is_biggest_neg = False #Swtich to flip cash and carry method based on apr signature
        idx = 0
        for perp in perp_pairs:
            perp_market = mango.market(context, perp)
            funding = perp_market.fetch_funding(context)
            apr = funding.extrapolated_apr * 100
            print("APR FOR {} is {}".format(perp,apr))
            if abs(apr) > abs(biggest_apr):
                biggest_apr = apr
                biggest_perp = perp
                biggest_spot_pair = spot_pairs[idx]
            idx += 1
        print("BIGGEST APR FOUND IS", biggest_apr)
        if biggest_apr < 0:
            is_biggest_neg = True
        return biggest_apr, biggest_perp, biggest_spot_pair, is_biggest_neg


def market_buy_spot(perp, spot, price_spot, quantity):
    print("\n")
    print("STARTING NEW POSITION WITH ", spot, price_spot)
    print("\n")
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        group = mango.Group.load(context)
        # Account created when mango is initiated in the UI
        account = mango.Account.load(context, PublicKey(ACCOUNT_1), group)
        perp_operation = mango.operations(context, wallet, account, perp, dry_run=False)
        spot_operation = mango.operations(context, wallet, account, spot, dry_run=False)
        try:
            print("LONGING SPOT")
            #Long spot
            order = mango.Order.from_values(
                side=mango.Side.BUY,
                price=decimal.Decimal(price_spot),
                quantity=decimal.Decimal(quantity),
                order_type=mango.OrderType.MARKET,
            )
            print("SPOT ORDER:", order)
            spot_order_sig = spot_operation.place_order(order)

            print(
                "WAITING FOR SPOT ORDER TO FULFIL...\n",
                spot_order_sig,
            )
            mango.WebSocketTransactionMonitor.wait_for_all(
                context.client.cluster_ws_url,
                spot_order_sig,
                commitment="processed",
            )

            print("SPOT ORDER SIG IS", spot_order_sig)
            success_spot_order = validate_tx(spot_order_sig)            
            print("EVAL {}".format(success_spot_order == True))
            retries = 0
            #Retry N times to push against Solana's constant dropped txs
            if success_spot_order != True:
                print("!!!!!!!!!!SPOT ORDER FAILED. RETRYING!!!!!!!!")
                while retries < 5 and success_spot_order == False:
                    print("RETRY ATTEMPT #{}".format(retries+1), order)
                    spot_order_sig = spot_operation.place_order(order)
                    print(
                        "WAITING FOR SPOT ORDER TO FULFIL...\n",
                        spot_order_sig,
                    )
                    mango.WebSocketTransactionMonitor.wait_for_all(
                        context.client.cluster_ws_url,
                        spot_order_sig,
                        commitment="processed",
                    )
                    success_spot_order = validate_tx(spot_order_sig)
                    retries += 1
            #Above failed. Try and evaluate again
            if success_spot_order != True:
                print("\n")
                print("CANNOT FINALIZE SPOT PURCHASE. REVERT ON MAIN!!")
                print("\n")
                return
            print("PROCESSED SPOT ORDER with TX", spot_order_sig)
        except:
            print("ERROR OCCURRED WHEN PROCESSING SPOT")


def market_sell_spot(perp, spot, price_spot, quantity):
    print("\n")
    print("STARTING NEW POSITION WITH ", spot, price_spot)
    print("\n")
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        group = mango.Group.load(context)        
        account = mango.Account.load(context, PublicKey(ACCOUNT_1), group)
        perp_operation = mango.operations(context, wallet, account, perp, dry_run=False)
        spot_operation = mango.operations(context, wallet, account, spot, dry_run=False)
        try:
            print("SHORTING SPOT")            
            order = mango.Order.from_values(
                side=mango.Side.SELL,
                price=decimal.Decimal(price_spot),
                quantity=decimal.Decimal(quantity),
                order_type=mango.OrderType.MARKET,
            )
            print("SPOT ORDER:", order)
            spot_order_sig = spot_operation.place_order(order)

            print(
                "WAITING FOR SPOT ORDER TO FULFIL...\n",
                spot_order_sig,
            )
            mango.WebSocketTransactionMonitor.wait_for_all(
                context.client.cluster_ws_url,
                spot_order_sig,
                commitment="processed",
            )

            print("SPOT ORDER SIG IS", spot_order_sig)
            success_spot_order = validate_tx(spot_order_sig)            
            print("EVAL {}".format(success_spot_order == True))
            retries = 0
            if success_spot_order != True:
                print("!!!!!!!!!!SPOT ORDER FAILED. RETRYING!!!!!!!!")
                while retries < 5 and success_spot_order == False:
                    print("RETRY ATTEMPT #{}".format(retries+1), order)
                    spot_order_sig = spot_operation.place_order(order)
                    print(
                        "WAITING FOR SPOT ORDER TO FULFIL...\n",
                        spot_order_sig,
                    )
                    mango.WebSocketTransactionMonitor.wait_for_all(
                        context.client.cluster_ws_url,
                        spot_order_sig,
                        commitment="processed",
                    )
                    success_spot_order = validate_tx(spot_order_sig)
                    retries += 1
            #Above failed. Try and evaluate again
            if success_spot_order != True:
                print("\n")
                print("CANNOT FINALIZE SPOT PURCHASE. REVERT ON MAIN!!")
                print("\n")
                return
            print("PROCESSED SPOT ORDER with TX", spot_order_sig)
        except:
            print("ERROR OCCURRED WHEN PROCESSING SPOT")



def market_sell_perp(perp, spot, price_spot, quantity):
    print("\n")
    print("STARTING NEW POSITION WITH ", perp, price_spot)
    print("\n")
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        group = mango.Group.load(context)
        # Account created when mango is initiated in the UI
        account = mango.Account.load(context, PublicKey(ACCOUNT_1), group)
        perp_operation = mango.operations(context, wallet, account, perp, dry_run=False)
        #Short perp
        order = mango.Order.from_values(
            side=mango.Side.SELL,
            price=decimal.Decimal(price_spot),
            quantity=decimal.Decimal(quantity),
            order_type=mango.OrderType.MARKET,
        )
        print("ORDER_ID IS", order)
        print("PERP ORDER:", order)
        perp_order_sig = perp_operation.place_order(order)
        print(
            "WAITING FOR PERP ORDER TO FULFIL...\n",
            perp_order_sig,
        )
        mango.WebSocketTransactionMonitor.wait_for_all(
            context.client.cluster_ws_url,
            perp_order_sig,
            commitment="processed",
        )
        success_perp_order = validate_tx(perp_order_sig)
        print("PERP OPERATION RESULT", success_perp_order == True)
        retries = 0
        if success_perp_order != True:
            print("!!!!!!!!!!PERP ORDER FAILED. RETRYING!!!!!!!!")
            while retries < 5 and success_perp_order == False:
                print("RETRY ATTEMPT #{}".format(retries+1), order)
                perp_order_sig = perp_operation.place_order(order)
                print(
                    "WAITING FOR PERP ORDER TO FULFIL...\n",
                    perp_order_sig,
                )
                mango.WebSocketTransactionMonitor.wait_for_all(
                    context.client.cluster_ws_url,
                    perp_order_sig,
                    commitment="processed",
                )
                perp_operation = validate_tx(perp_order_sig)
                retries += 1
        #Above failed. Try and evaluate again
        if success_perp_order != True:
            print("\n")
            print("CANNOT FINALIZE PERP OPEN. REVERT ON MAIN!!")
            print("\n")
            return

        print("PROCESSED PERP ORDER WITH TX", perp_order_sig)


#Long perp 
def market_buy_perp(perp, spot, price_spot, quantity):
    print("\n")
    print("STARTING NEW POSITION WITH ", perp, price_spot)
    print("\n")
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        group = mango.Group.load(context)        
        account = mango.Account.load(context, PublicKey(ACCOUNT_1), group)
        perp_operation = mango.operations(context, wallet, account, perp, dry_run=False)        
        order = mango.Order.from_values(
            side=mango.Side.BUY,
            price=decimal.Decimal(price_spot),
            quantity=decimal.Decimal(quantity),
            order_type=mango.OrderType.MARKET,
        )
        print("ORDER_ID IS", order)
        print("PERP ORDER:", order)
        perp_order_sig = perp_operation.place_order(order)
        print(
            "WAITING FOR PERP ORDER TO FULFIL...\n",
            perp_order_sig,
        )
        mango.WebSocketTransactionMonitor.wait_for_all(
            context.client.cluster_ws_url,
            perp_order_sig,
            commitment="processed",
        )
        success_perp_order = validate_tx(perp_order_sig)
        print("PERP OPERATION RESULT", success_perp_order == True)
        retries = 0
        if success_perp_order != True:
            print("!!!!!!!!!!PERP ORDER FAILED. RETRYING!!!!!!!!")
            while retries < 5 and success_perp_order == False:
                print("RETRY ATTEMPT #{}".format(retries+1), order)
                perp_order_sig = perp_operation.place_order(order)
                print(
                    "WAITING FOR PERP ORDER TO FULFIL...\n",
                    perp_order_sig,
                )
                mango.WebSocketTransactionMonitor.wait_for_all(
                    context.client.cluster_ws_url,
                    perp_order_sig,
                    commitment="processed",
                )
                perp_operation = validate_tx(perp_order_sig)
                retries += 1
        #Above failed. Try and evaluate again
        if success_perp_order != True:
            print("\n")
            print("CANNOT FINALIZE PERP OPEN. REVERT ON MAIN!!")
            print("\n")
            return

        print("PROCESSED PERP ORDER WITH TX", perp_order_sig)






def sell_close_perp_buy_close_spot(perp, spot, spot_price, quantity):
    #Sell perp to close position. Sells close a current long perp direction. 
    #Buy spot to close position. Buying spot repays the loan used to short the spot position
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        group = mango.Group.load(context)
        # Account created when mango is initiated in the UI
        account = mango.Account.load(context, PublicKey(ACCOUNT_1), group)
        market_operations = mango.operations(
            context, wallet, account, perp, dry_run=False
        )
        #Close perp position
        market_operations = mango.operations(
            context, wallet, account, perp, dry_run=False
        )
        order = mango.Order.from_values(
            side=mango.Side.SELL,
            price=decimal.Decimal(spot_price),
            quantity=decimal.Decimal(quantity),
            order_type=mango.OrderType.MARKET,
            reduce_only=True
        )
        print("PERP ORDER:", order)
        placed_order_signatures = market_operations.place_order(order)

        print(
            "EXECUTING PERP SELL ORDER...\n",
            placed_order_signatures,
        )
        #mango.Account.deposit        
        mango.WebSocketTransactionMonitor.wait_for_all(
            context.client.cluster_ws_url,
            placed_order_signatures,
            commitment="processed",
        )
        print("PERP ORDER SELL TX", placed_order_signatures)

        #Buy spot
        market_operations = mango.operations(
            context, wallet, account, spot, dry_run=False
        )
        order = mango.Order.from_values(
            side=mango.Side.BUY,
            price=decimal.Decimal(spot_price),
            quantity=decimal.Decimal(quantity),
            order_type=mango.OrderType.MARKET,
        )
        print("SPOT ORDER:", order)
        placed_order_signatures = market_operations.place_order(order)

        print(
            "EXECUTING SPOT SELL ORDER...\n",
            placed_order_signatures,
        )
        mango.WebSocketTransactionMonitor.wait_for_all(
            context.client.cluster_ws_url,
            placed_order_signatures,
            commitment="processed",
        )
        print("SPOT ORDER SELL TX", placed_order_signatures)


def buy_close_perp_sell_close_spot(perp, spot, spot_price, quantity):
    #Opposite of the function above
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        group = mango.Group.load(context)
        # Account created when mango is initiated in the UI
        account = mango.Account.load(context, PublicKey(ACCOUNT_1), group)
        market_operations = mango.operations(
            context, wallet, account, perp, dry_run=False
        )
        #Close perp position
        market_operations = mango.operations(
            context, wallet, account, perp, dry_run=False
        )
        order = mango.Order.from_values(
            side=mango.Side.BUY,
            price=decimal.Decimal(spot_price),
            quantity=decimal.Decimal(quantity),
            order_type=mango.OrderType.MARKET,
            reduce_only=True
        )
        print("PERP ORDER:", order)
        placed_order_signatures = market_operations.place_order(order)

        print(
            "EXECUTING PERP SELL ORDER...\n",
            placed_order_signatures,
        )
        #mango.Account.deposit        
        mango.WebSocketTransactionMonitor.wait_for_all(
            context.client.cluster_ws_url,
            placed_order_signatures,
            commitment="processed",
        )
        print("perp ORDER SELL TX", placed_order_signatures)

        #Sell spot
        market_operations = mango.operations(
            context, wallet, account, spot, dry_run=False
        )
        order = mango.Order.from_values(
            side=mango.Side.SELL,
            price=decimal.Decimal(spot_price),
            quantity=decimal.Decimal(quantity),
            order_type=mango.OrderType.MARKET,
        )
        print("SPOT ORDER:", order)
        placed_order_signatures = market_operations.place_order(order)

        print(
            "EXECUTING SPOT SELL ORDER...\n",
            placed_order_signatures,
        )
        mango.WebSocketTransactionMonitor.wait_for_all(
            context.client.cluster_ws_url,
            placed_order_signatures,
            commitment="processed",
        )
        print("SPOT ORDER SELL TX", placed_order_signatures)


def reedeem_pnl():
    with mango.ContextBuilder.build(cluster_name="mainnet") as context:
        group = mango.Group.load(context)
        cache: mango.Cache = mango.Cache.load(context, group.cache)

        account = mango.Account.load(context, PublicKey(ACCOUNT_1), group)
        reedem_signatures = account.redeem_all_perp_pnl(context, wallet, group, cache)
        print("\n")
        print("REEDEEMING PNL")
        print("Waiting for reedem transaction to confirm...", reedem_signatures)
        mango.WebSocketTransactionMonitor.wait_for_all(
            context.client.cluster_ws_url, reedem_signatures, commitment="processed"
        )        
        print("COMPLETE TX", reedem_signatures)
        print("\n")


def generate_yield():    
    print("*** RUNNING GENERATE_YIELD() FUNCTION ***")
    #Check funding rates, compare with current position
    curr_apr, curr_pos, old_apr = get_open_orders()
    #If current position is open, update the funding for next
    perp_apr, perp, spot, is_biggest_neg = query_apr()    
    if spot is None:
        print("NO +VE FUNDING FOUND")
        return
    spot_price = get_price(spot)
    #TODO: Change quantity according to preferance. Better yet, control via .env file
    quantity = QUANTITY / spot_price
    print("=== PERP FOUND IS === {}".format(perp))
    print("=== HIGHEST APR FOUND FOR PERP IS {} ===".format(perp_apr))
    print("=== CURRENT POSITION IS {} WITH FUNDING OF {} ===".format(curr_pos, curr_apr))
    #If biggest APR is negative, we want to arb +VE funding
    print("IS_BIGGEST_NEG", is_biggest_neg)
    #No position found, open a new one
    if curr_apr is None and is_biggest_neg == False:
        print("QUANTITY AT OPEN", quantity)
        print("OPENING A NEW POSITIVE FUNDING POSITION")  
        market_buy_spot(perp, spot, spot_price,  quantity)        
        market_sell_perp(perp, spot, spot_price,  quantity)       
        funding = get_funding(perp)
        print("FUNDING IS", funding)
        curr_pos =  perp.split("-")[0]
        track_positions(funding, curr_pos)
        return  
    elif curr_apr is None and is_biggest_neg == True:
        print("QUANTITY AT OPEN", quantity)
        print("OPENING A NEW NEGATIVE FUNDING POSITION")  
        market_sell_spot(perp, spot, spot_price,  quantity)        
        market_buy_perp(perp, spot, spot_price,  quantity)       
        funding = get_funding(perp)
        print("FUNDING IS", funding)
        curr_pos =  perp.split("-")[0]
        track_positions(funding, curr_pos)
        return   
    
    #If position flips on us and is still the highest. 
    #Happens if we are currently in a perp-short position
    print("CHECK", curr_apr > 0 and perp_apr < 0, curr_apr, perp_apr)
    print("CONDITION 1", curr_apr > 0 and perp_apr < 0 and "{}/USDC".format(curr_pos) == spot)
    if curr_apr > 0 and old_apr < 0 and "{}/USDC".format(curr_pos) == spot:
        print("\n")
        print("SWITCHING POSITIONS")
        print("CURRENT POS {}".format(curr_pos))
        curr_quantity = QUANTITY/get_price("{}/USDC".format(curr_pos))
        buy_close_perp_sell_close_spot("{}-PERP".format(curr_pos), "{}/USDC".format(curr_pos), spot_price, curr_quantity)    
        market_buy_spot(perp, spot, spot_price, quantity)
        market_sell_perp(perp, spot, spot_price, quantity)     
        funding = get_funding(perp)
        curr_pos =  perp.split("-")[0]
        track_positions(funding, curr_pos)   
    #If position flips on us and is still the highest. Happens if we are currently in a perp-long position
    elif curr_apr < 0 and old_apr > 0 and "{}/USDC".format(curr_pos) == spot:
        print("\n")
        print("SWITCHING POSITIONS")
        print("CURRENT POS {}".format(curr_pos))
        curr_quantity = QUANTITY/get_price("{}/USDC".format(curr_pos))
        sell_close_perp_buy_close_spot("{}-PERP".format(curr_pos), "{}/USDC".format(curr_pos), spot_price, curr_quantity) 
        market_buy_spot(perp, spot, spot_price, quantity)
        market_sell_perp(perp, spot, spot_price, quantity)     
        funding = get_funding(perp)
        curr_pos =  perp.split("-")[0]
        track_positions(funding, curr_pos)   
    elif curr_apr and curr_pos:
        #TODO: Use TWAP
        #Get difference between curr apr and highest apr out there. If > 80%, we should switch positions
        diff = ((abs(perp_apr) - abs(curr_apr)) / abs(curr_apr)) * 100
        #If perp found is better than the current position, close out current, open new position
        #Make sure new position is not the same as the old position. Do for +VE funding
        print("ENSURING that {}/USDC !== {}".format(curr_pos, spot))
        if diff > THRESHOLD  and "{}/USDC".format(curr_pos) != spot and is_biggest_neg == False:
            print("\n")
            print("SWITCHING POSITIONS")
            print("CURRENT POS {}".format(curr_pos))
            print("EXECUTING buy_close_perp_sell_close_spot")
            curr_quantity = QUANTITY/get_price("{}/USDC".format(curr_pos))
            buy_close_perp_sell_close_spot("{}-PERP".format(curr_pos), "{}/USDC".format(curr_pos), spot_price, curr_quantity)    
            market_buy_spot(perp, spot, spot_price, quantity)
            market_sell_perp(perp, spot, spot_price, quantity)     
            funding = get_funding(perp)
            curr_pos =  perp.split("-")[0]
            track_positions(funding, curr_pos)   
        #-VE funding
        elif diff > THRESHOLD and "{}/USDC".format(curr_pos) != spot and is_biggest_neg == True:
            print("\n")
            print("SWITCHING POSITIONS")
            print("CURRENT POS {}".format(curr_pos))
            print("EXECUTING sell_close_perp_buy_close_spot")
            curr_quantity = QUANTITY/get_price("{}/USDC".format(curr_pos))
            sell_close_perp_buy_close_spot("{}-PERP".format(curr_pos), "{}/USDC".format(curr_pos), spot_price, curr_quantity)    
            market_sell_spot(perp, spot, spot_price, quantity)
            market_buy_perp(perp, spot, spot_price, quantity)     
            funding = get_funding(perp)
            curr_pos =  perp.split("-")[0]
            track_positions(funding, curr_pos)   
        #TODO: HANDLE CASES WHERE THE APR FLIPS ON THE CURRENT POSITION            
        else:
            print("STAYING IN CURRENT POSITION {}".format(curr_pos))
                      

runs = 0
while 1:
    now = datetime.now()
    print("\n")
    print("EVALUATING APRS")
    print("TIME IS {} ... EPOCH {}".format(now.strftime("%H:%M:%S"), runs))
    generate_yield()
    dt = datetime.now() + timedelta(minutes=60)    #TODO PREVENT RATE LIMITTING
    while datetime.now() < dt:
        time.sleep(1)
    print("\n\n\n")
    print("END OF EPOCH # {}".format(runs))
    runs += 1



