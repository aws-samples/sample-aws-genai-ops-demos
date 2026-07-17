import {
  AccountClient,
  GetContactInformationCommand,
  GetAlternateContactCommand,
  AlternateContactType,
} from '@aws-sdk/client-account';

const accountClient = new AccountClient({});

export interface DefaultContacts {
  rootEmail: string | null;
  alternateContacts: AlternateContact[];
  resolvedAt: string;
  source: 'aws-account-api';
}

export interface AlternateContact {
  type: string;
  name: string | null;
  email: string | null;
  phone: string | null;
  title: string | null;
}

/**
 * Resolves default notification contacts from the AWS Account API.
 * Used as a fallback when no team routing configuration or contacts are provided.
 *
 * Returns:
 * - Root account email (from contact information)
 * - Alternate contacts (Operations, Security, Billing)
 */
export const handler = async (): Promise<DefaultContacts> => {
  console.log('Resolving default contacts from AWS Account API (no team routing configured)');

  const result: DefaultContacts = {
    rootEmail: null,
    alternateContacts: [],
    resolvedAt: new Date().toISOString(),
    source: 'aws-account-api',
  };

  // Fetch primary contact information (root email)
  try {
    const contactInfo = await accountClient.send(new GetContactInformationCommand({}));
    if (contactInfo.ContactInformation?.FullName) {
      result.rootEmail = contactInfo.ContactInformation.FullName
        ? `${contactInfo.ContactInformation.FullName} (Account Primary Contact)`
        : null;

      console.log(`Primary contact resolved: ${contactInfo.ContactInformation.FullName}`);
    }
  } catch (error: any) {
    if (error.name === 'AccessDeniedException') {
      console.warn('No permission to read account contact information (account:GetContactInformation)');
    } else {
      console.error('Failed to fetch primary contact information:', error.message);
    }
  }

  // Fetch alternate contacts (Operations, Security, Billing)
  const contactTypes = [
    AlternateContactType.OPERATIONS,
    AlternateContactType.SECURITY,
    AlternateContactType.BILLING,
  ];

  for (const contactType of contactTypes) {
    try {
      const response = await accountClient.send(
        new GetAlternateContactCommand({ AlternateContactType: contactType })
      );

      if (response.AlternateContact) {
        const contact: AlternateContact = {
          type: contactType,
          name: response.AlternateContact.Name || null,
          email: response.AlternateContact.EmailAddress || null,
          phone: response.AlternateContact.PhoneNumber || null,
          title: response.AlternateContact.Title || null,
        };
        result.alternateContacts.push(contact);
        console.log(`Alternate contact resolved: ${contactType} → ${contact.email || 'no email'}`);
      }
    } catch (error: any) {
      if (error.name === 'ResourceNotFoundException') {
        console.log(`No alternate contact configured for type: ${contactType}`);
      } else if (error.name === 'AccessDeniedException') {
        console.warn(`No permission to read alternate contact: ${contactType} (account:GetAlternateContact)`);
      } else {
        console.error(`Failed to fetch alternate contact ${contactType}:`, error.message);
      }
    }
  }

  const emailCount = result.alternateContacts.filter(c => c.email).length;
  console.log(`Default contact resolution complete: ${emailCount} email(s) found from alternate contacts`);

  return result;
};
