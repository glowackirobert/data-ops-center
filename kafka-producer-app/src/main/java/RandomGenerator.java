import java.math.BigInteger;
import java.util.Random;

public class RandomGenerator {

    private static final Random RANDOM = new Random();
    private static final String CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

    public static String generateStringValue() {
        var sb = new StringBuilder(5);
        for (int i = 0; i < 5; i++) {
            var randomIndex = RANDOM.nextInt(CHARACTERS.length());
            sb.append(CHARACTERS.charAt(randomIndex));
        }
        return sb.toString();
    }

    public static BigInteger generateBigIntegerValue(int bits) {
        return new BigInteger(bits, RANDOM);
    }

    public static boolean generateBooleanValue() {
        return RANDOM.nextBoolean();
    }
}
