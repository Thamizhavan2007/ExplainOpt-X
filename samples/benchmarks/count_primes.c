#include <stdio.h>
int main(void) {
    int count = 0;
    for (int i = 2; i < 200000; i++) {
        int prime = 1;
        for (int j = 2; j * j <= i; j++) {
            if (i % j == 0) {
                prime = 0;
                break;
            }
        }
        count += prime;
    }
    printf("%d\n", count);
    return 0;
}
