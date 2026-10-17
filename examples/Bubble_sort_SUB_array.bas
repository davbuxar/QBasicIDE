DECLARE SUB BubbleSort (a(), n%)
DIM nums(5)
DATA 42, 7, 19, 3, 25, 11
FOR i = 0 TO 5: READ nums(i): NEXT i
CALL BubbleSort(nums(), 5)
FOR i = 0 TO 5
    PRINT nums(i);
NEXT i
PRINT
END

SUB BubbleSort (a(), n%)
    FOR i = 0 TO n% - 1
        FOR j = 0 TO n% - 1 - i
            IF a(j) > a(j + 1) THEN SWAP a(j), a(j + 1)
        NEXT j
    NEXT i
END SUB
