total = 0
FOR i = 1 TO 5
    READ x
    total = total + x
NEXT i
PRINT "Average ="; total / 5
PRINT USING "Formatted: ###.##"; total / 5
DATA 12, 18, 7, 30, 21
