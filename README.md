# FundSurf


![alt text](https://xvsiitlrwsnmkmyktqfu.supabase.co/storage/v1/object/sign/bukky/fundingsample.png?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1cmwiOiJidWtreS9mdW5kaW5nc2FtcGxlLnBuZyIsImlhdCI6MTY1NTEyNTQ0MiwiZXhwIjoxOTcwNDg1NDQyfQ.DJBZJekZfgVibZFK0KxR9rxWnL4YVEnCnYAmXC5L3Xc)
Example of returns. Green bars represent days of positive funding payments auto deposited to the mango account on a balance of $30 [8% Interest per year]



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
