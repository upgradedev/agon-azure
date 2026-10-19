import { ScreenHeader, View } from "../components/screen";
import { ButtonLink } from "../components/ui/button";
import { hrefFor } from "../lib/router";

export function NotFound({ active }: { active: boolean }) {
  return (
    <View view="not-found" active={active}>
      <ScreenHeader
        view="not-found"
        eyebrow="Not found"
        title="There is no page at this address"
        lead="The link may be mistyped, or it names a run that is not in the runs index."
      />
      <ButtonLink href={hrefFor("home")}>Go to the Match Centre</ButtonLink>
    </View>
  );
}
