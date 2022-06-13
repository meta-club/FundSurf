# FundSurf

Steps to run:

1) Create .env file with the following format:
PRIVATE_KEY= -------
ENV=mainnet
ACCOUNT_1= ----------
SUPABASE_URL= ---------
SUPABASE_KEY= ---------

Private key is the private key of your Solana wallet.
Account 1 is the public Key of your Solana wallet

2) Create supabase table with the following schema:
 id: int
 funding: float8
 curr_pos: text/varchar
 created_at: datetime
 
3)  Deposit funds into mango account https://trade.mango.markets/account
4)  Run pip3 install requirements.txt
5)  Run python3 main.py
