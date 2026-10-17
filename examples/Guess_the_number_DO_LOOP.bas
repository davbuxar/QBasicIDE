RANDOMIZE TIMER
secret = INT(RND * 100) + 1
tries = 0
DO
    INPUT "Guess (1-100)"; g
    tries = tries + 1
    IF g < secret THEN
        PRINT "Too low"
    ELSEIF g > secret THEN
        PRINT "Too high"
    END IF
LOOP UNTIL g = secret
PRINT "Correct in"; tries; "tries!"
